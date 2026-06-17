import bpy
import mathutils
import bmesh
import itertools
import os
import os.path
import re

from . import FmdlFile, FmdlMeshSplitting, FmdlSplitVertexEncoding, Ftex, PesSkeletonData


_PES_DEBUG_LOG_PATH = None

def _pesDebugLog(message):
	global _PES_DEBUG_LOG_PATH
	if not _PES_DEBUG_LOG_PATH:
		return
	try:
		with open(_PES_DEBUG_LOG_PATH, 'a', encoding = 'utf-8') as _f:
			_f.write(str(message) + "\n")
			_f.flush()
	except Exception:
		pass


class UnsupportedFmdl(Exception):
	pass

class FmdlExportError(Exception):
	def __init__(self, errors):
		if isinstance(errors, list):
			self.errors = errors
		else:
			self.errors = [errors]

class ImportSettings:
	def __init__(self):
		self.enableExtensions = True
		self.enableVertexLoopPreservation = True
		self.enableMeshSplitting = True
		self.enableLoadTextures = True
		self.enableImportAllBoundingBoxes = False
		self.armatureName = str()
		self.meshIdName = str()
		self.fixMeshsmooth = True

class ExportSettings:
	def __init__(self):
		self.enableExtensions = True
		self.enableVertexLoopPreservation = True
		self.enableMeshSplitting = True
		self.meshIdName = str()


def createBoundingBox(context, meshObject, min, max):
	name = "Bounding box for %s" % meshObject.data.name
	objectID = meshObject.name
	
	blenderLattice = bpy.data.lattices.new(name)
	blenderLattice.points_u = 2
	blenderLattice.points_v = 2
	blenderLattice.points_w = 2
	# The default constructed (2,2,2)-lattice has a size of 1x1x1 centered around the origin.
	# Scale and translate this to the desired coordinates using a transformation matrix.
	# This translation matrix has a scaling factors on the diagonal, and translation offsets
	# on the bottom row, applied _after_ scaling.
	matrix = [[0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 1]]
	for i in range(3):
		size = max[i] - min[i]
		if size < 0.000001:
			size = 0.000001
		matrix[i][i] = size
		basePosition = -size / 2
		matrix[3][i] = min[i] - basePosition
	blenderLattice.transform(matrix)
	
	blenderLatticeObject = bpy.data.objects.new(name, blenderLattice)
	blenderLatticeObject.parent = bpy.data.objects[objectID]
	context.collection.objects.link(blenderLatticeObject)
	context.view_layer.update()

def createFittingBoundingBox(context, meshObject):
	transformedMesh = meshObject.data.copy()
	transformedMesh.transform(meshObject.matrix_world)
	transformedMeshObject = bpy.data.objects.new('measurement', transformedMesh)
	boundingBox = transformedMeshObject.bound_box
	minCoordinates = tuple(min([boundingBox[j][i] for j in range(8)]) for i in range(3))
	maxCoordinates = tuple(max([boundingBox[j][i] for j in range(8)]) for i in range(3))
	bpy.data.objects.remove(transformedMeshObject)
	bpy.data.meshes.remove(transformedMesh)
	
	createBoundingBox(context, meshObject, minCoordinates, maxCoordinates)

def importFmdl(context, fmdl, filename, importSettings = None):
	UV_MAP_COLOR = 'UVMap'
	UV_MAP_NORMALS = 'normal_map'
	global _PES_DEBUG_LOG_PATH
	try:
		_PES_DEBUG_LOG_PATH = os.path.join(os.path.expanduser('~'), 'pes_import_debug.log')
		with open(_PES_DEBUG_LOG_PATH, 'w', encoding = 'utf-8') as _f:
			_f.write("PES import debug log\n")
			_f.write("filename: %s\n" % filename)
	except Exception:
		_PES_DEBUG_LOG_PATH = None
	_pesDebugLog("importFmdl: start")
	def findTexture(texture, textureSearchPath):
		textureFilename = texture.directory.replace('\\', '/').rstrip('/') + '/' + texture.filename.replace('\\', '/').lstrip('/')
		textureFilenameComponents = tuple(filter(None, textureFilename.split('/')))

		if len(textureFilenameComponents) == 0:
			return None
		filename = textureFilenameComponents[-1]
		directory = textureFilenameComponents[:-1]
		directorySuffixes = [directory[i:] for i in range(len(directory) + 1)]
		
		if filename == 'kit.dds':
			filenames = []
		else:
			filenames = [filename]
			position = filename.rfind('.')
			if position >= 0:
				for extension in ['dds', 'tga', 'ftex']:
					modifiedFilename = filename[:position + 1] + extension
					if modifiedFilename not in filenames:
						filenames.append(modifiedFilename)
		
		for searchDirectory in textureSearchPath:
			for suffix in directorySuffixes:
				for filename in filenames:
					fullFilename = os.path.join(searchDirectory, *suffix, filename)
					if os.path.isfile(fullFilename):
						return fullFilename
				
				if len(filenames) == 0:
					directory = os.path.join(searchDirectory, *suffix)
					
					if not os.path.isdir(directory):
						continue
					
					try:
						entries = os.listdir(directory)
					except:
						continue
					for entry in entries:
						if re.match(r'^u[0-9]{4}p1.dds$', entry, flags = re.IGNORECASE):
							fullFilename = os.path.join(directory, entry)
							if os.path.isfile(fullFilename):
								print(entry)
								return fullFilename
		
		return None

	def findTextureRecursive(texture, facePath):
		# Robust fallback texture finder, mirroring the proven reference viewer
		# (_find_texture_file): walk the model folder and a few ancestor folders
		# and match by file stem (exact first, then fuzzy). This is what lets
		# eyelash / eyebrow / overlay textures load even when they are not in the
		# single hardcoded #windx11 path, instead of falling back to a flat colour.
		try:
			stem = os.path.splitext(os.path.basename((texture.filename or '').replace('\\', '/')))[0]
		except Exception:
			stem = ''
		if not stem or not facePath:
			return None
		stemLow = stem.lower()
		roots = []
		try:
			d = os.path.dirname(facePath)
			for _ in range(4):
				if d and os.path.isdir(d) and d not in roots:
					roots.append(d)
				nd = os.path.dirname(d)
				if not nd or nd == d:
					break
				d = nd
		except Exception:
			pass
		exts = ('.ftex', '.dds', '.tga', '.png')
		# Pass 1: exact stem match.
		for root in roots:
			try:
				for dirpath, _dn, files in os.walk(root):
					for f in files:
						fstem, fext = os.path.splitext(f.lower())
						if fext in exts and fstem == stemLow:
							return os.path.join(dirpath, f)
			except Exception:
				continue
		# Pass 2: fuzzy match (names contain each other), skip coeff parameter maps.
		for root in roots:
			try:
				for dirpath, _dn, files in os.walk(root):
					for f in files:
						fstem, fext = os.path.splitext(f.lower())
						if fext not in exts or 'coeff' in fstem:
							continue
						if stemLow and (stemLow in fstem or fstem in stemLow):
							return os.path.join(dirpath, f)
			except Exception:
				continue
		return None

	def addTexture(blenderMaterial, textureRole, texture, textureIDs, uvMapColor, uvMapNormals, textureSearchPath,
				   loadTextures):
		identifier = (textureRole, texture)
		texture_path = context.scene.face_path[:-29] + "\\sourceimages\\#windx11\\" + texture.filename
		if identifier in textureIDs:
			blenderTexture = bpy.data.textures[textureIDs[identifier]]
		else:
			if texture.filename in bpy.data.images:
				blenderImage = bpy.data.images[texture.filename]
			else:
				blenderImage = bpy.data.images.new(texture.filename, width=0, height=0)
			blenderImage.source = 'FILE'

			if '_SRGB' in textureRole:
				blenderImage.colorspace_settings.name = 'sRGB'
			elif '_LIN' in textureRole:
				blenderImage.colorspace_settings.name = 'Linear Rec.709'
			else:
				blenderImage.colorspace_settings.name = 'Non-Color'

			if loadTextures:
				filename = findTexture(texture, textureSearchPath)
				if filename == None:
					_pesDebugLog("addTexture: role=%s NOT found in searchPath, guessPath=%s exists=%s" % (textureRole, texture_path, os.path.isfile(texture_path)))
					try:
						ok = Ftex.blenderImageLoadAny(blenderImage, texture_path, bpy.app.tempdir, _pesDebugLog)
						_pesDebugLog("addTexture: role=%s guessPath load ok=%s size=%s" % (textureRole, ok, tuple(blenderImage.size)))
					except Exception as _e:
						_pesDebugLog("addTexture: guessPath load failed for %s: %s" % (texture_path, _e))
				else:
					_pesDebugLog("addTexture: role=%s FOUND=%s" % (textureRole, filename))
					try:
						ok = Ftex.blenderImageLoadAny(blenderImage, filename, bpy.app.tempdir, _pesDebugLog)
						_pesDebugLog("addTexture: role=%s load ok=%s size=%s" % (textureRole, ok, tuple(blenderImage.size)))
					except Exception as _e:
						_pesDebugLog("addTexture: load failed for %s: %s" % (filename, _e))

				# Last-resort recursive search (reference-style) when the texture still
				# did not load. Fixes white eyelashes/eyebrows whose textures live
				# outside the single #windx11 path.
				try:
					loadedOk = tuple(blenderImage.size)[0] > 0
				except Exception:
					loadedOk = False
				if not loadedOk:
					rec = findTextureRecursive(texture, context.scene.face_path)
					if rec:
						_pesDebugLog("addTexture: role=%s RECURSIVE found=%s" % (textureRole, rec))
						try:
							ok = Ftex.blenderImageLoadAny(blenderImage, rec, bpy.app.tempdir, _pesDebugLog)
							_pesDebugLog("addTexture: role=%s recursive load ok=%s size=%s" % (textureRole, ok, tuple(blenderImage.size)))
						except Exception as _e:
							_pesDebugLog("addTexture: recursive load failed for %s: %s" % (rec, _e))
					else:
						_pesDebugLog("addTexture: role=%s recursive search found nothing for stem of %s" % (textureRole, texture.filename))
			
			textureName = "[%s] %s" % (textureRole, texture.filename)
			blenderTexture = bpy.data.textures.new(textureName, type='IMAGE')
			blenderTexture.image = blenderImage

			textureIDs[identifier] = blenderTexture.name

		if '_NRM' in textureRole:
			slotUvLayer = uvMapNormals
		else:
			slotUvLayer = uvMapColor

		blenderTextureSlot = blenderMaterial.fmdl_texture_slots.add()
		blenderTextureSlot.texture = blenderTexture
		blenderTextureSlot.uv_layer = slotUvLayer

		blenderTexture.fmdl_texture_filename = texture.filename
		blenderTexture.fmdl_texture_directory = texture.directory
		blenderTexture.fmdl_texture_role = textureRole
	
	def materialHasSeparateUVMaps(materialInstance, fmdl):
		for mesh in fmdl.meshes:
			if mesh.materialInstance == materialInstance:
				if mesh.vertexFields.uvCount >= 1 and 1 not in mesh.vertexFields.uvEqualities[0]:
					return True
		return False
	
	def setupMaterialNodes(blenderMaterial):
		# Build a PES-style skin/hair shader node tree using all available FMDL
		# texture maps (Base, Normal, Specular/Roughness SRM, Translucent) so shading
		# approximates the original PES Fox shader in Blender 2.8+ / 4.x / 5.x.
		# Socket names are resolved version-safely (Blender 3.x vs 4.x/5.x renamed
		# several Principled BSDF inputs).
		try:
			blenderMaterial.use_nodes = True
		except Exception:
			return
		nodeTree = blenderMaterial.node_tree
		if nodeTree is None:
			return
		nodes = nodeTree.nodes
		links = nodeTree.links
		
		principled = None
		output = None
		for node in nodes:
			if node.type == 'BSDF_PRINCIPLED':
				principled = node
			elif node.type == 'OUTPUT_MATERIAL':
				output = node
		if principled is None:
			principled = nodes.new('ShaderNodeBsdfPrincipled')
			principled.location = (0, 0)
		if output is None:
			output = nodes.new('ShaderNodeOutputMaterial')
			output.location = (400, 0)
		if not any(link.from_node == principled and link.to_node == output for link in links):
			links.new(principled.outputs['BSDF'], output.inputs['Surface'])
		
		def linkInput(fromSocket, candidateNames):
			for n in candidateNames:
				if n in principled.inputs:
					try:
						links.new(fromSocket, principled.inputs[n])
						return True
					except Exception:
						pass
			return False
		
		def setInput(value, candidateNames):
			for n in candidateNames:
				if n in principled.inputs:
					try:
						principled.inputs[n].default_value = value
						return True
					except Exception:
						pass
			return False
		
		def imageLoaded(img):
			if img is None:
				return False
			try:
				if tuple(img.size)[0] > 0:
					return True
			except Exception:
				pass
			try:
				p = bpy.path.abspath(img.filepath) if img.filepath else ''
				return bool(p) and os.path.isfile(p)
			except Exception:
				return False
		
		# Collect images by FMDL texture role
		baseImage = None
		normalImage = None
		specularImage = None
		roughnessImage = None
		translucentImage = None
		firstImage = None
		firstColorImage = None
		for slot in blenderMaterial.fmdl_texture_slots:
			if slot is None or slot.texture is None or slot.texture.image is None:
				continue
			role = slot.texture.fmdl_texture_role or ''
			image = slot.texture.image
			try:
				fn = (slot.texture.fmdl_texture_filename or image.name or '').lower()
			except Exception:
				fn = ''
			# PES "coeff" entries are shader-parameter maps, not real colour textures
			# (often with no file on disk). The proven reference viewer explicitly
			# skips them when choosing a base/albedo texture.
			isCoeff = 'coeff' in fn
			# Skip occlusion maps (eye shadow overlays) – they are NOT colour textures
			# and must never become the base-colour fallback.
			isOccl = 'occl' in fn
			if firstImage is None and not isOccl:
				firstImage = image
			if '_NRM' in role and normalImage is None:
				normalImage = image
			elif 'Roughness' in role and roughnessImage is None:
				roughnessImage = image
			elif 'Specular' in role and specularImage is None:
				specularImage = image
			elif 'Translucent' in role and translucentImage is None:
				translucentImage = image
			elif ('Base' in role or '_SRGB' in role) and baseImage is None and not isCoeff:
				baseImage = image
			# Track the first plausible colour texture (not normal/spec/rough/coeff)
			# as a robust albedo fallback for materials whose base role is missing or
			# points to a coeff parameter map (some eyelash / eyebrow materials).
			if (firstColorImage is None and not isCoeff and not isOccl
					and '_NRM' not in role and 'Roughness' not in role and 'Specular' not in role
					and not any(k in fn for k in ('nrm', 'norm', 'srm', 'spec', 'msk', 'mask'))):
				firstColorImage = image
		if baseImage is None:
			baseImage = firstColorImage if firstColorImage is not None else firstImage
		if roughnessImage is None:
			roughnessImage = specularImage  # PES SRM serves both specular & roughness
		
		name = (blenderMaterial.name or '').lower()
		# IMPORTANT: PES face BSM textures (face_bsm_alp) store a NON-opacity mask in their
		# alpha channel that blacks out the central face region. That alpha must NEVER be
		# used as transparency on the skin / head / head-shell, or the centre of the face
		# renders black (the RGB is actually a full, correct skin face). So skin/head/shell
		# are OPAQUE and ignore the BSM alpha. Only true see-through parts (hair, eyelash,
		# eye, oral, glass) use their alpha.
		isShell = 'shell' in name
		# Eye overlays (lashes, brows, eyeline, eyeshadow, eyelid, occlusion shadow)
		# are thin DARK cutout cards. Detected independently of skin so they never
		# fall back to the bright non-skin white below.
		isEyeOverlay = any(k in name for k in ('lash', 'eyeline', 'eyebrow', 'brow', 'eyeshadow', 'eyelid', 'occlusion'))
		# The eyeball itself (eyeball / cornea / sclera / iris). This must NEVER be
		# treated as skin: skin uses subsurface scattering with a red radius, which on
		# the small rounded eyeball renders as glowing RED eyes.
		isEye = (not isEyeOverlay) and any(k in name for k in ('eyeball', 'cornea', 'sclera', 'iris', 'eye'))
		isSkin = (('skin' in name) or ('head' in name) or ('face' in name) or isShell) and (not isEye) and (not isEyeOverlay)
		isHair = ('hair' in name) and (not isSkin) and (not isEye) and (not isEyeOverlay)
		isTransparent = (not isSkin) and (isEye or isEyeOverlay or any(k in name for k in ('hair', 'oral', 'glass')))
		
		try:
			_pesDebugLog("setupMaterialNodes: material=%s base=%s baseSize=%s normal=%s rough=%s spec=%s trans=%s transparent=%s skin=%s" % (
				blenderMaterial.name,
				(baseImage.filepath if baseImage is not None else None),
				(tuple(baseImage.size) if baseImage is not None else None),
				(normalImage.filepath if normalImage is not None else None),
				(roughnessImage.filepath if roughnessImage is not None else None),
				(specularImage.filepath if specularImage is not None else None),
				(translucentImage.filepath if translucentImage is not None else None),
				isTransparent, isSkin,
			))
		except Exception:
			pass
		
		# Base color (+ alpha only for transparent materials such as hair / eyelash)
		if imageLoaded(baseImage):
			try:
				baseImage.colorspace_settings.name = 'sRGB'
			except Exception:
				pass
			# For opaque skin/head/shell materials, force Blender to ignore the BSM alpha
			# channel entirely (it is a detail/region mask, not opacity). Without this the
			# central face renders black wherever the mask alpha is low.
			if not isTransparent:
				try:
					baseImage.alpha_mode = 'NONE'
				except Exception:
					pass
			baseNode = nodes.new('ShaderNodeTexImage')
			baseNode.image = baseImage
			baseNode.location = (-700, 300)
			linkInput(baseNode.outputs['Color'], ['Base Color'])
			if isTransparent:
				linkInput(baseNode.outputs['Alpha'], ['Alpha'])
		else:
			# Base texture missing / not loaded: use a neutral colour instead of
			# leaving an unconnected image node (renders bright magenta in EEVEE).
			if isSkin:
				setInput([0.80, 0.62, 0.50, 1.0], ['Base Color'])
			elif isEyeOverlay:
				setInput([0.02, 0.02, 0.02, 1.0], ['Base Color'])
			elif isHair:
				setInput([0.05, 0.04, 0.03, 1.0], ['Base Color'])
			else:
				setInput([0.80, 0.80, 0.80, 1.0], ['Base Color'])
		
		# Blend mode (version-safe across EEVEE and EEVEE-Next 4.2+/5.x)
		try:
			blenderMaterial.blend_method = 'HASHED' if isTransparent else 'OPAQUE'
		except Exception:
			pass
		try:
			# Hair & eye overlays = solid cutout strands -> DITHERED (like HASHED/CLIP).
			# Blended mode on hair makes semi-transparent alpha pixels show through,
			# producing scattered holes and a "see-through" look in EEVEE-Next 4.2+.
			_srm = 'DITHERED' if (isHair or isEyeOverlay) else ('BLENDED' if isTransparent else 'DITHERED')
			blenderMaterial.surface_render_method = _srm
		except Exception:
			pass
		
		# Roughness map (SRM) for NON-skin materials (hair, eyelash, eye, collar) -> Roughness.
		# Skin / head use a fixed matte roughness (set below) so the face reads as skin
		# instead of glossy dark metal in EEVEE-Next.
		if imageLoaded(roughnessImage) and not isSkin:
			try:
				roughnessImage.colorspace_settings.name = 'Non-Color'
			except Exception:
				pass
			roughNode = nodes.new('ShaderNodeTexImage')
			roughNode.image = roughnessImage
			roughNode.location = (-700, 0)
			sepNode = None
			try:
				sepNode = nodes.new('ShaderNodeSeparateColor')
			except Exception:
				sepNode = None
			if sepNode is not None:
				sepNode.location = (-400, 0)
				links.new(roughNode.outputs['Color'], sepNode.inputs['Color'])
				roughOut = sepNode.outputs['Green']
				if isHair:
					# Clamp hair roughness to a matte floor so glossy SRM hotspots don't
					# read as wet/blue plastic highlights under studio lighting.
					try:
						maxNode = nodes.new('ShaderNodeMath')
						maxNode.operation = 'MAXIMUM'
						maxNode.location = (-200, 0)
						# Floor raised to 0.85: keeps hair matte, kills glossy environment
						# reflection hotspots (grey swirly patches) in Material Preview.
						maxNode.inputs[1].default_value = 0.85
						links.new(sepNode.outputs['Green'], maxNode.inputs[0])
						roughOut = maxNode.outputs['Value']
					except Exception:
						roughOut = sepNode.outputs['Green']
				if not linkInput(roughOut, ['Roughness']):
					linkInput(roughNode.outputs['Color'], ['Roughness'])
			else:
				linkInput(roughNode.outputs['Color'], ['Roughness'])
		
		# Normal map -> Normal Map node -> Normal
		if imageLoaded(normalImage):
			try:
				normalImage.colorspace_settings.name = 'Non-Color'
			except Exception:
				pass
			normalNode = nodes.new('ShaderNodeTexImage')
			normalNode.image = normalImage
			normalNode.location = (-700, -300)
			normalMap = nodes.new('ShaderNodeNormalMap')
			normalMap.location = (-400, -300)
			# Hair: soften the normal so the near-black strands don't catch swirly
			# HDRI reflections in Material Preview (the "grey swirl" artifact). The
			# base color is uniformly near-black (albedo ~0.09), so any normal-shaped
			# reflection of the studio light is the brightest thing visible.
			if isHair:
				try:
					normalMap.inputs['Strength'].default_value = 0.5
				except Exception:
					pass
			# PES normal maps are frequently 2-channel (X in red, Y in green) with an
			# empty blue channel. Feeding that raw makes Blender read Z=-1 (the normal
			# points INTO the surface) -> dark, patchy, broken shading (very visible on
			# hair). Reconstruct Z = sqrt(1 - x^2 - y^2) from R,G. For a valid tangent
			# normal this yields an identical Z, so it is safe for every normal map
			# (including the face's BC5 normals).
			try:
				try:
					sep = nodes.new('ShaderNodeSeparateColor')
				except Exception:
					sep = nodes.new('ShaderNodeSeparateRGB')
				sep.location = (-650, -480)
				try:
					comb = nodes.new('ShaderNodeCombineColor')
				except Exception:
					comb = nodes.new('ShaderNodeCombineRGB')
				comb.location = (-470, -480)
				def _mathNode(op, x=-560, y=-480):
					n = nodes.new('ShaderNodeMath'); n.operation = op; n.location = (x, y)
					return n
				nx = _mathNode('MULTIPLY_ADD'); nx.inputs[1].default_value = 2.0; nx.inputs[2].default_value = -1.0
				ny = _mathNode('MULTIPLY_ADD'); ny.inputs[1].default_value = 2.0; ny.inputs[2].default_value = -1.0
				nx2 = _mathNode('MULTIPLY'); ny2 = _mathNode('MULTIPLY')
				sumxy = _mathNode('ADD')
				oneminus = _mathNode('SUBTRACT'); oneminus.inputs[0].default_value = 1.0
				nz = _mathNode('SQRT')
				bz = _mathNode('MULTIPLY_ADD'); bz.inputs[1].default_value = 0.5; bz.inputs[2].default_value = 0.5
				links.new(normalNode.outputs['Color'], sep.inputs[0])
				links.new(sep.outputs[0], nx.inputs[0])
				links.new(sep.outputs[1], ny.inputs[0])
				links.new(nx.outputs[0], nx2.inputs[0]); links.new(nx.outputs[0], nx2.inputs[1])
				links.new(ny.outputs[0], ny2.inputs[0]); links.new(ny.outputs[0], ny2.inputs[1])
				links.new(nx2.outputs[0], sumxy.inputs[0]); links.new(ny2.outputs[0], sumxy.inputs[1])
				links.new(sumxy.outputs[0], oneminus.inputs[1])
				links.new(oneminus.outputs[0], nz.inputs[0])
				links.new(nz.outputs[0], bz.inputs[0])
				links.new(sep.outputs[0], comb.inputs[0])
				links.new(sep.outputs[1], comb.inputs[1])
				links.new(bz.outputs[0], comb.inputs[2])
				links.new(comb.outputs[0], normalMap.inputs['Color'])
			except Exception:
				links.new(normalNode.outputs['Color'], normalMap.inputs['Color'])
			linkInput(normalMap.outputs['Normal'], ['Normal'])
		
		# Translucency map (TRM) -> Subsurface Color (like the 2.92 "TRM Subsurface"
		# group, whose Saturation 2.0 boosts the translucent skin colour).
		# Only on Blender versions that still expose a 'Subsurface Color' input (3.x).
		if isSkin and imageLoaded(translucentImage) and ('Subsurface Color' in principled.inputs):
			try:
				translucentImage.colorspace_settings.name = 'sRGB'
			except Exception:
				pass
			trmNode = nodes.new('ShaderNodeTexImage')
			trmNode.image = translucentImage
			trmNode.location = (-700, -600)
			hsvNode = None
			try:
				hsvNode = nodes.new('ShaderNodeHueSaturation')
			except Exception:
				hsvNode = None
			if hsvNode is not None:
				hsvNode.location = (-400, -600)
				try:
					hsvNode.inputs['Saturation'].default_value = 2.0
				except Exception:
					pass
				links.new(trmNode.outputs['Color'], hsvNode.inputs['Color'])
				linkInput(hsvNode.outputs['Color'], ['Subsurface Color'])
			else:
				linkInput(trmNode.outputs['Color'], ['Subsurface Color'])
		
		# Make sure nothing reads as metal.
		setInput(0.0, ['Metallic'])
		# Defensive: NO non-skin material may carry subsurface scattering. A leftover
		# subsurface weight with the red skin radius is exactly what makes the eyeball
		# (and other thin parts) glow red.
		if not isSkin:
			setInput(0.0, ['Subsurface Weight', 'Subsurface'])
		# Skin / head: matte roughness + soft subsurface + low specular so the face
		# reads as skin (matches the 2.92 PES look), not glossy dark metal.
		if isSkin:
			setInput(0.62, ['Roughness'])
			setInput(0.1, ['Subsurface Weight', 'Subsurface'])
			# Keep the subsurface radius SMALL. PES heads import at a tiny scale, so a
			# large red radius makes small / concave parts (eyeballs, eye sockets,
			# nostrils) glow bright red. A few-mm radius gives soft skin WITHOUT the
			# red oversaturation.
			setInput([0.010, 0.005, 0.003], ['Subsurface Radius'])
			# The skin albedo is very dark (~0.05 linear). Under the bright Material-Preview
			# studio light, ANY specular reflects a neutral highlight that washes the dark
			# brown base toward flat grey (the classic "grey face" symptom). Drop skin
			# specular to near-zero so the warm brown diffuse dominates, matching the matte
			# 2.92 PES look. Also zero the Specular Tint so no extra neutral sheen is added.
			setInput(0.05, ['Specular IOR Level', 'Specular'])
			setInput([1.0, 1.0, 1.0, 1.0], ['Specular Tint'])
		elif isEye:
			# Eyeball / cornea: a wet, glossy dielectric. No subsurface (already zeroed
			# above), a clean specular highlight, and a fairly smooth surface.
			setInput(0.30, ['Specular IOR Level', 'Specular'])
			setInput([1.0, 1.0, 1.0, 1.0], ['Specular Tint'])
			setInput(0.30, ['Roughness'])
		elif isHair:
			# Hair: keep a little sheen but kill the plastic/blue specular hotspot.
			# Low specular + neutral tint + a matte roughness fallback (used when no
			# SRM roughness map is connected; when SRM is present it is floored above).
			# Specular dimatikan & roughness matte agar rambut tidak plastik/mengkilap
			# dan tidak memantulkan swirl HDRI di Material Preview.
			setInput(0.0, ['Specular IOR Level', 'Specular'])
			setInput([1.0, 1.0, 1.0, 1.0], ['Specular Tint'])
			setInput(0.90, ['Roughness'])
		else:
			setInput(0.4, ['Specular IOR Level', 'Specular'])
	
	def importMaterials(fmdl, textureSearchPath, loadTextures):
		materialIDs = {}
		textureIDs = {}
		
		for materialInstance in fmdl.materialInstances:
			blenderMaterial = bpy.data.materials.new(materialInstance.name)
			materialIDs[materialInstance] = blenderMaterial.name
			
			blenderMaterial.fmdl_material_shader = materialInstance.shader
			blenderMaterial.fmdl_material_technique = materialInstance.technique
			
			for (name, values) in materialInstance.parameters:
				blenderMaterialParameter = blenderMaterial.fmdl_material_parameters.add()
				blenderMaterialParameter.name = name
				blenderMaterialParameter.parameters = [v for v in values]
			
			uvMapColor = UV_MAP_COLOR
			if materialHasSeparateUVMaps(materialInstance, fmdl):
				uvMapNormals = UV_MAP_NORMALS
			else:
				uvMapNormals = UV_MAP_COLOR
			
			
			for (role, texture) in materialInstance.textures:
				addTexture(blenderMaterial, role, texture, textureIDs, uvMapColor, uvMapNormals, textureSearchPath, loadTextures)
			
			if loadTextures:
				setupMaterialNodes(blenderMaterial)
		
		return materialIDs
	
	def addBone(blenderArmature, bone, boneIDs, bonesByName):
		if bone in boneIDs:
			return boneIDs[bone]
		
		useConnect = False
		if bone.name in PesSkeletonData.bones:
			pesBone = PesSkeletonData.bones[bone.name]
			(headX, headY, headZ) = pesBone.startPosition
			(tailX, tailY, tailZ) = pesBone.endPosition
			head = (headX, -headZ, headY)
			tail = (tailX, -tailZ, tailY)
			parentBoneName = pesBone.renderParent
			while parentBoneName is not None and parentBoneName not in bonesByName:
				parentBoneName = PesSkeletonData.bones[parentBoneName].renderParent
			if parentBoneName is None:
				parentBone = None
			else:
				parentBone = bonesByName[parentBoneName]
				parentDistanceSquared = sum(((PesSkeletonData.bones[parentBoneName].endPosition[i] - pesBone.startPosition[i]) ** 2 for i in range(3)))
				if parentBoneName == pesBone.renderParent and parentDistanceSquared < 0.0000000001:
					useConnect = True
		else:
			tail = (bone.globalPosition.x, -bone.globalPosition.z, bone.globalPosition.y)
			head = (bone.localPosition.x, -bone.localPosition.z, bone.localPosition.y)
			parentBone = bone.parent
		
		if parentBone != None:
			parentBoneID = addBone(blenderArmature, parentBone, boneIDs, bonesByName)
		else:
			parentBoneID = None
		
		if sum(((tail[i] - head[i]) ** 2 for i in range(3))) < 0.0000000001:
			tail = (head[0], head[1], head[2] - 0.00001)
		
		blenderEditBone = blenderArmature.edit_bones.new(bone.name)
		boneID = blenderEditBone.name
		boneIDs[bone] = boneID
		
		blenderEditBone.head = head
		blenderEditBone.tail = tail
		blenderEditBone.hide = False
		if parentBoneID != None:
			blenderEditBone.parent = blenderArmature.edit_bones[parentBoneID]
			blenderEditBone.use_connect = useConnect
		
		return boneID
	
	def importSkeleton(context, fmdl):
		sklname = importSettings.armatureName
		blenderArmature = bpy.data.armatures.new(sklname)
		blenderArmature.show_names = True
		
		blenderArmatureObject = bpy.data.objects.new(sklname, blenderArmature)
		armatureObjectID = blenderArmatureObject.name
		
		context.collection.objects.link(blenderArmatureObject)
		context.view_layer.objects.active = blenderArmatureObject
		
		bpy.ops.object.mode_set(mode = 'EDIT')
		
		bonesByName = {}
		for bone in fmdl.bones:
			bonesByName[bone.name] = bone
		
		boneIDs = {}
		for bone in fmdl.bones:
			addBone(blenderArmature, bone, boneIDs, bonesByName)
		
		bpy.ops.object.mode_set(mode = 'OBJECT')
		bpy.data.objects[armatureObjectID].hide_set(True)
		return (armatureObjectID, boneIDs)
	
	def addSkeletonMeshModifier(blenderMeshObject, boneGroup, armatureObjectID, boneIDs):
		blenderArmatureObject = bpy.data.objects[armatureObjectID]
		blenderArmature = blenderArmatureObject.data
		
		blenderModifier = blenderMeshObject.modifiers.new("fmdl skeleton", type = 'ARMATURE')
		blenderModifier.object = blenderArmatureObject
		blenderModifier.use_vertex_groups = True
		
		vertexGroupIDs = {}
		for bone in boneGroup.bones:
			blenderBone = blenderArmature.bones[boneIDs[bone]]
			blenderVertexGroup = blenderMeshObject.vertex_groups.new(name = blenderBone.name)
			vertexGroupIDs[bone] = blenderVertexGroup.name
		return vertexGroupIDs
	
	def findUvMapImage(blenderMaterial, uvMapName, rolePrefix):
		options = []
		for slot in blenderMaterial.fmdl_texture_slots:
			if slot is None:
				continue
			if slot.uv_layer != uvMapName:
				continue
			if (
				    slot.texture is not None
				and slot.texture.type == 'IMAGE'
				and slot.texture.image is not None
				and slot.texture.image.size[0] != 0
			):
				image = slot.texture.image
			else:
				image = None
			options.append((image, slot.texture.fmdl_texture_role))
		
		for (image, role) in options:
			if role.lower().startswith(rolePrefix.lower()):
				return image
		if len(options) > 0:
			return options[0][0]
		return None
	
	def importMesh(mesh, name, fmdl, materialIDs, armatureObjectID, boneIDs):
		_pesDebugLog("importMesh: START name=%s vertices=%d faces=%d hasNormal=%s hasColor=%s uvCount=%s hasBoneMapping=%s" % (name, len(mesh.vertices), len(mesh.faces), mesh.vertexFields.hasNormal, mesh.vertexFields.hasColor, mesh.vertexFields.uvCount, mesh.vertexFields.hasBoneMapping))
		blenderMesh = bpy.data.meshes.new(name)
		
		#
		# mesh.vertices does not correspond either to the blenderMesh.vertices
		# nor the blenderMesh.loops, but rather the unique values of blenderMesh.loops.
		# The blenderMesh.vertices correspond to the unique vertex.position values in mesh.vertices.
		#
		
		vertexIndices = {}
		vertexVertices = []
		for vertex in mesh.vertices:
			if vertex.position not in vertexIndices:
				vertexIndices[vertex.position] = len(vertexIndices)
				vertexVertices.append(vertex)
		loopVertices = list(itertools.chain.from_iterable([reversed(face.vertices) for face in mesh.faces]))
		
		# Build the mesh topology with from_pydata, which correctly initializes the
		# loop/polygon offset storage used by Blender 4.1+. The old low-level
		# loops.add()/polygons.foreach_set("loop_start"/"loop_total") approach
		# produces a corrupt mesh on 4.x and crashes Blender.
		blenderMesh.from_pydata(
			[(vertex.position.x, -vertex.position.z, vertex.position.y) for vertex in vertexVertices],
			[],
			[
				(
					vertexIndices[loopVertices[3 * faceIndex + 0].position],
					vertexIndices[loopVertices[3 * faceIndex + 1].position],
					vertexIndices[loopVertices[3 * faceIndex + 2].position],
				)
				for faceIndex in range(len(mesh.faces))
			],
		)
		blenderMesh.polygons.foreach_set("use_smooth", [importSettings.fixMeshsmooth] * len(blenderMesh.polygons))
		
		blenderMesh.update(calc_edges = True)
		blenderMesh.validate(clean_customdata = False)
		_pesDebugLog("importMesh: geometry OK loops=%d polys=%d" % (len(blenderMesh.loops), len(blenderMesh.polygons)))
		
		blenderMaterial = bpy.data.materials[materialIDs[mesh.materialInstance]]
		
		if mesh.vertexFields.hasNormal:
			def normalize(vector):
				(x, y, z) = vector
				size = (x ** 2 + y ** 2 + z ** 2) ** 0.5
				if size < 0.01:
					return (x, y, z)
				return (x / size, y / size, z / size)
			loopNormals = [
				normalize((vertex.normal.x, -vertex.normal.z, vertex.normal.y)) for vertex in loopVertices
			]
			_pesDebugLog("importMesh: normals before (count=%d loops=%d)" % (len(loopNormals), len(blenderMesh.loops)))
			if len(loopNormals) == len(blenderMesh.loops):
				blenderMesh.normals_split_custom_set(loopNormals)
			_pesDebugLog("importMesh: normals OK")
		
		_pesDebugLog("importMesh: before colors")
		if mesh.vertexFields.hasColor:
			blenderMesh.color_attributes.new('Edit', 'BYTE_COLOR', 'CORNER')
			colorLayer = blenderMesh.color_attributes.new('Anim', 'BYTE_COLOR', 'CORNER')
			colorLayer.data.foreach_set("color", tuple(itertools.chain.from_iterable([
				(tuple(vertex.color[0:4]) if len(vertex.color) >= 4 else tuple(vertex.color[0:3]) + (1.0,)) for vertex in loopVertices
			])))
		
		_pesDebugLog("importMesh: before uv")
		if mesh.vertexFields.uvCount >= 1:
			uvLayer = blenderMesh.uv_layers.new(name = UV_MAP_COLOR)
			
			uvLayer.data.foreach_set("uv", tuple(itertools.chain.from_iterable([
				(vertex.uv[0].u, 1.0 - vertex.uv[0].v) for vertex in loopVertices
			])))
			uvLayer.active = True
			uvLayer.active_render = True
		
		if mesh.vertexFields.uvCount >= 2 and 0 not in mesh.vertexFields.uvEqualities[1]:
			uvLayer = blenderMesh.uv_layers.new(name = UV_MAP_NORMALS)
			
			uvLayer.data.foreach_set("uv", tuple(itertools.chain.from_iterable([
				(vertex.uv[1].u, 1.0 - vertex.uv[1].v) for vertex in loopVertices
			])))
		
		if mesh.vertexFields.uvCount >= 3:
			raise UnsupportedFmdl("No support for fmdl files with more than 2 UV maps")
		
		_pesDebugLog("importMesh: before material append")
		blenderMesh.materials.append(blenderMaterial)
		_pesDebugLog("importMesh: material appended")
		
		blenderMesh.fmdl_alpha_enum = mesh.alphaEnum
		blenderMesh.fmdl_shadow_enum = mesh.shadowEnum
		_pesDebugLog("importMesh: enums set")
		
		blenderMeshObject = bpy.data.objects.new(blenderMesh.name, blenderMesh)
		meshObjectID = blenderMeshObject.name
		_pesDebugLog("importMesh: object created, linking to scene...")
		context.collection.objects.link(blenderMeshObject)
		_pesDebugLog("importMesh: object linked OK")
		
		if mesh.vertexFields.hasBoneMapping:
			_pesDebugLog("importMesh: before skeleton modifier")
			vertexGroupIDs = addSkeletonMeshModifier(blenderMeshObject, mesh.boneGroup, armatureObjectID, boneIDs)
			for i in range(len(vertexVertices)):
				for bone in vertexVertices[i].boneMapping:
					weight = vertexVertices[i].boneMapping[bone]
					blenderMeshObject.vertex_groups[vertexGroupIDs[bone]].add((i, ), weight, 'REPLACE')
		
		_pesDebugLog("importMesh: DONE name=%s" % name)
		return meshObjectID
	
	def importMeshes(context, fmdl, materialIDs, armatureObjectID, boneIDs):
		meshNames = {}
		for meshGroup in fmdl.meshGroups:
			if len(meshGroup.meshes) == 1 and meshGroup.name != "":
				meshNames[meshGroup.meshes[0]] = meshGroup.name
		nextIndex = 0
		meshIdName = importSettings.meshIdName
		for mesh in fmdl.meshes:
			if mesh not in meshNames:
				meshNames[mesh] = "{0}_{1}".format(meshIdName, nextIndex)
				nextIndex += 1
		meshObjectIDs = {}
		for mesh in fmdl.meshes:
			meshObjectIDs[mesh] = importMesh(mesh, meshNames[mesh], fmdl, materialIDs, armatureObjectID, boneIDs)
		
		return meshObjectIDs
	
	def addMeshGroup(context, meshGroup, meshObjectIDs, importBoundingBoxMode):
		if len(meshGroup.meshes) == 0 and len(meshGroup.children) == 1 and meshGroup.name == "":
			return addMeshGroup(context, meshGroup.children[0], meshObjectIDs, importBoundingBoxMode)
		
		if len(meshGroup.meshes) == 1:
			blenderMeshGroupObject = bpy.data.objects[meshObjectIDs[meshGroup.meshes[0]]]
		else:
			blenderMeshGroupObject = bpy.data.objects.new(meshGroup.name, None)
			context.collection.objects.link(blenderMeshGroupObject)
			
			for mesh in meshGroup.meshes:
				bpy.data.objects[meshObjectIDs[mesh]].parent = blenderMeshGroupObject
		
		for mesh in meshGroup.meshes:
			if (
				   importBoundingBoxMode == 'ALL'
				or (importBoundingBoxMode == 'CUSTOM' and 'custom-bounding-box-meshes' in mesh.extensionHeaders)
			):
				minCoordinates = (
					meshGroup.boundingBox.min.x,
					-meshGroup.boundingBox.max.z,
					meshGroup.boundingBox.min.y,
				)
				maxCoordinates = (
					meshGroup.boundingBox.max.x,
					-meshGroup.boundingBox.min.z,
					meshGroup.boundingBox.max.y,
				)
				createBoundingBox(context, bpy.data.objects[meshObjectIDs[mesh]], minCoordinates, maxCoordinates)
		
		meshGroupID = blenderMeshGroupObject.name
		for child in meshGroup.children:
			childID = addMeshGroup(context, child, meshObjectIDs, importBoundingBoxMode)
			bpy.data.objects[childID].parent = bpy.data.objects[meshGroupID]
		
		return meshGroupID
	
	def importMeshTree(context, fmdl, meshObjectIDs, armatureObjectID, filename, importBoundingBoxMode):
		rootMeshGroups = []
		for meshGroup in fmdl.meshGroups:
			if meshGroup.parent == None:
				rootMeshGroups.append(meshGroup)
		
		dirname = os.path.basename(os.path.dirname(filename))
		basename = os.path.basename(filename)
		position = basename.rfind('.')
		if position == -1:
			name = os.path.join(dirname, basename)
		else:
			name = os.path.join(dirname, basename[:position])
		
		rootMeshGroup = FmdlFile.FmdlFile.MeshGroup()
		rootMeshGroup.name = name
		rootMeshGroup.parent = None
		rootMeshGroup.children = rootMeshGroups
		rootMeshGroup.meshes = []
		
		blenderMeshGroupID = addMeshGroup(context, rootMeshGroup, meshObjectIDs, importBoundingBoxMode)
		
		if armatureObjectID != None:
			bpy.data.objects[armatureObjectID].parent = bpy.data.objects[blenderMeshGroupID]
		
		bpy.data.objects[blenderMeshGroupID].fmdl_file = True
		bpy.data.objects[blenderMeshGroupID].fmdl_filename = filename
		
		return blenderMeshGroupID
	
	
	
	if importSettings == None:
		importSettings = ImportSettings()
	
	if context.active_object == None:
		activeObjectID = None
	else:
		activeObjectID = bpy.data.objects.find(context.active_object.name)
	if context.mode != 'OBJECT':
		bpy.ops.object.mode_set(mode = 'OBJECT')
	
	
	
	if importSettings.enableExtensions and importSettings.enableMeshSplitting:
		fmdl = FmdlMeshSplitting.decodeFmdlSplitMeshes(fmdl)
	if importSettings.enableExtensions and importSettings.enableVertexLoopPreservation:
		fmdl = FmdlSplitVertexEncoding.decodeFmdlVertexLoopPreservation(fmdl)
	
	if importSettings.enableImportAllBoundingBoxes:
		importBoundingBoxMode = 'ALL'
	elif importSettings.enableExtensions:
		importBoundingBoxMode = 'CUSTOM'
	else:
		importBoundingBoxMode = 'NONE'
	
	baseDir = os.path.dirname(filename)
	textureSearchPath = []
	for directory in [
		baseDir,
		os.path.dirname(baseDir),
		os.path.dirname(os.path.dirname(baseDir)),
		os.path.join(baseDir, 'Common'),
		os.path.join(os.path.dirname(baseDir), 'Common'),
		os.path.join(os.path.dirname(os.path.dirname(baseDir)), 'Common'),
		os.path.join(baseDir, 'Kit Textures'),
		os.path.join(os.path.dirname(baseDir), 'Kit Textures'),
		os.path.join(os.path.dirname(os.path.dirname(baseDir)), 'Kit Textures'),
	]:
		if os.path.isdir(directory):
			textureSearchPath.append(directory)
	_pesDebugLog("importFmdl: before importMaterials")
	materialIDs = importMaterials(fmdl, textureSearchPath, importSettings.enableLoadTextures)
	_pesDebugLog("importFmdl: after importMaterials, bones=%d" % len(fmdl.bones))
	
	if len(fmdl.bones) > 0:
		(armatureObjectID, boneIDs) = importSkeleton(context, fmdl)
	else:
		(armatureObjectID, boneIDs) = (None, [])
	_pesDebugLog("importFmdl: after importSkeleton")
	
	meshObjectIDs = importMeshes(context, fmdl, materialIDs, armatureObjectID, boneIDs)
	_pesDebugLog("importFmdl: after importMeshes")
	
	rootMeshGroupID = importMeshTree(context, fmdl, meshObjectIDs, armatureObjectID, filename, importBoundingBoxMode)
	_pesDebugLog("importFmdl: after importMeshTree")
	
	# --- Eye fixup pass -------------------------------------------------------
	# PES eyeballs (objects named eyeL / eyeR / eye / iris / sclera / cornea)
	# share the skin_face shader with the face, so setupMaterialNodes classifies
	# them as skin and gives them red subsurface scattering -> the eyeball renders
	# as a glowing red disc. We cannot tell them apart from the face by material
	# name or shader, but we CAN tell by the object name. Force those objects'
	# materials to a clean, glossy, NON-subsurface eyeball look.
	try:
		eyeKeywords = ('eyeball', 'cornea', 'sclera', 'iris', 'eye')
		eyeOverlayKeywords = ('lash', 'brow', 'line', 'shadow', 'lid', 'occlusion')
		for _mesh in meshObjectIDs:
			blenderObject = bpy.data.objects.get(meshObjectIDs[_mesh])
			if blenderObject is None or blenderObject.data is None:
				continue
			objName = (blenderObject.name or '').lower()
			if any(k in objName for k in eyeOverlayKeywords):
				continue
			if not any(k in objName for k in eyeKeywords):
				continue
			for blenderMaterial in blenderObject.data.materials:
				if blenderMaterial is None or not blenderMaterial.use_nodes:
					continue
				principled = None
				for node in blenderMaterial.node_tree.nodes:
					if node.type == 'BSDF_PRINCIPLED':
						principled = node
						break
				if principled is None:
					continue
				def _eyeSet(value, names, _principled = principled):
					for inputName in names:
						if inputName in _principled.inputs:
							try:
								_principled.inputs[inputName].default_value = value
								return True
							except:
								pass
					return False
				# Kill subsurface -> removes the red glow entirely.
				_eyeSet(0.0, ['Subsurface Weight', 'Subsurface'])
				# Clean wet glossy dielectric eyeball.
				_eyeSet(0.0, ['Metallic'])
				_eyeSet(0.30, ['Roughness'])
				_eyeSet(0.5, ['Specular IOR Level', 'Specular'])
				# Opaque eyeball (no see-through).
				_eyeSet(1.0, ['Alpha'])
				try:
					blenderMaterial.blend_method = 'OPAQUE'
				except:
					pass
				_pesDebugLog("eye fixup: object=%s material=%s -> subsurface killed, glossy eye applied" % (blenderObject.name, blenderMaterial.name))
	except Exception as _eyeErr:
		_pesDebugLog("eye fixup failed: %s" % str(_eyeErr))
	
	
	
	if context.mode != 'OBJECT':
		bpy.ops.object.mode_set(mode = 'OBJECT')
	if activeObjectID != None:
		blenderArmatureObject = bpy.data.objects[activeObjectID]
	
	return bpy.data.objects[rootMeshGroupID]


def exportFmdl(context, rootObjectName, exportSettings = None):
	global _PES_DEBUG_LOG_PATH
	try:
		_PES_DEBUG_LOG_PATH = os.path.join(os.path.expanduser('~'), 'pes_export_debug.log')
		with open(_PES_DEBUG_LOG_PATH, 'w', encoding = 'utf-8') as _f:
			_f.write('PES export debug log\n')
	except:
		_PES_DEBUG_LOG_PATH = None
	_pesDebugLog('exportFmdl: start root=%s' % rootObjectName)
	
	def exportMaterial(blenderMaterial, textureFmdlObjects):
		materialInstance = FmdlFile.FmdlFile.MaterialInstance()
		
		for slot in blenderMaterial.fmdl_texture_slots:
			if slot == None:
				continue
			blenderTexture = slot.texture
			if blenderTexture not in textureFmdlObjects:
				texture = FmdlFile.FmdlFile.Texture()
				texture.filename = blenderTexture.fmdl_texture_filename
				texture.directory = blenderTexture.fmdl_texture_directory
				textureFmdlObjects[blenderTexture] = texture
			materialInstance.textures.append((blenderTexture.fmdl_texture_role, textureFmdlObjects[blenderTexture]))
		
		for parameter in blenderMaterial.fmdl_material_parameters:
			materialInstance.parameters.append((parameter.name, [v for v in parameter.parameters]))
		
		materialInstance.name = blenderMaterial.name
		materialInstance.shader = blenderMaterial.fmdl_material_shader
		materialInstance.technique = blenderMaterial.fmdl_material_technique
		
		return materialInstance
	
	def exportMaterials(blenderMeshObjects):
		blenderMaterials = []
		for blenderMeshObject in blenderMeshObjects:
			blenderMesh = blenderMeshObject.data
			for blenderMaterial in blenderMesh.materials:
				if blenderMaterial is not None and blenderMaterial not in blenderMaterials:
					blenderMaterials.append(blenderMaterial)
		
		materialInstances = []
		materialFmdlObjects = {}
		textureFmdlObjects = {}
		for blenderMaterial in blenderMaterials:
			materialInstance = exportMaterial(blenderMaterial, textureFmdlObjects)
			materialInstances.append(materialInstance)
			materialFmdlObjects[blenderMaterial] = materialInstance
		
		return (materialInstances, materialFmdlObjects)
	
	def exportBone(name, parent, location):
		bone = FmdlFile.FmdlFile.Bone()
		bone.name = name
		bone.parent = parent
		if parent is not None:
			parent.children.append(bone)
		(x, y, z) = location
		bone.globalPosition = FmdlFile.FmdlFile.Vector4(x, y, z, 1.0)
		bone.localPosition = FmdlFile.FmdlFile.Vector4(0.0, 0.0, 0.0, 0.0)
		# Fill in bone.boundingBox later
		return bone
	
	def exportBones(blenderMeshObjects):
		def findBone(boneName, armatures):
			if boneName in PesSkeletonData.bones:
				pesBone = PesSkeletonData.bones[boneName]
				return (boneName, pesBone.sklParent, pesBone.startPosition)
			
			for armature in armatures:
				for blenderBone in armature.bones:
					if blenderBone.name == boneName:
						if blenderBone.parent is None:
							parentName = None
						else:
							parentName = blenderBone.parent.name
						(headX, headY, headZ) = blenderBone.head_local
						return (boneName, parentName, (headX, headZ, -headY))
			
			return (boneName, None, (0, 0, 0))
		
		blenderArmatures = []
		blenderMeshArmatures = {}
		for blenderMeshObject in blenderMeshObjects:
			blenderMeshArmatures[blenderMeshObject] = []
			for modifier in blenderMeshObject.modifiers:
				if modifier.type == 'ARMATURE':
					blenderArmature = modifier.object.data
					blenderArmatures.append(blenderArmature)
					blenderMeshArmatures[blenderMeshObject].append(blenderArmature)
		
		bones = {}
		for blenderMeshObject in blenderMeshObjects:
			boneNames = [vertexGroup.name for vertexGroup in blenderMeshObject.vertex_groups]
			armatures = (
				blenderMeshArmatures[blenderMeshObject] +
				[armature for armature in blenderArmatures if armature not in blenderMeshArmatures[blenderMeshObject]]
			)
			for boneName in boneNames:
				if boneName not in bones:
					bones[boneName] = findBone(boneName, armatures)
		for blenderArmature in blenderArmatures:
			for blenderBone in blenderArmature.bones:
				boneName = blenderBone.name
				if boneName not in bones:
					bones[boneName] = findBone(boneName, [blenderArmature])
		
		orderedBones = []
		bonesByName = {}
		def addOrderedBone(boneName):
			if boneName in bonesByName:
				return
			(name, parentName, location) = bones[boneName]
			if parentName is not None and parentName not in bones:
				parentName = None
			if parentName is not None:
				addOrderedBone(parentName)
				parent = bonesByName[parentName]
			else:
				parent = None
			bone = exportBone(name, parent, location)
			orderedBones.append(bone)
			bonesByName[boneName] = bone
		for boneName in bones.keys():
			addOrderedBone(boneName)
		
		return (orderedBones, bonesByName)
	
	def exportMeshGeometry(blenderMeshObject, colorLayer, uvLayerColor, uvLayerNormal, boneVector, scene):
		#
		# Setup a modified version of the mesh data that can be fiddled with.
		#
		_pesDebugLog('exportMeshGeometry: START name=%s' % blenderMeshObject.name)
		modifiedBlenderMesh = blenderMeshObject.data.copy()
		
		#
		# Apply mesh-object position and orientation
		#
		modifiedBlenderMesh.transform(blenderMeshObject.matrix_world)
		
		loopTotals = [0 for i in range(len(modifiedBlenderMesh.polygons))]
		modifiedBlenderMesh.polygons.foreach_get("loop_total", loopTotals)
		if len(loopTotals) > 0 and max(loopTotals) != 3:
			#
			# calc_tangents() only works on triangulated meshes
			#
			_pesDebugLog('exportMeshGeometry: triangulating')
			bm = bmesh.new()
			bm.from_mesh(modifiedBlenderMesh)
			bmesh.ops.triangulate(bm, faces = bm.faces[:])
			bm.to_mesh(modifiedBlenderMesh)
			bm.free()
			modifiedBlenderMesh.update()
		
		# Blender 4.x: ensure the mesh is internally consistent before
		# calc_tangents(), which can hard-crash on unvalidated geometry.
		modifiedBlenderMesh.validate(clean_customdata = False)
		
		if uvLayerNormal is None:
			uvLayerTangent = uvLayerColor
		else:
			uvLayerTangent = uvLayerNormal
		_pesDebugLog('exportMeshGeometry: before calc_tangents uvmap=%s loops=%d' % (uvLayerTangent, len(modifiedBlenderMesh.loops)))
		modifiedBlenderMesh.calc_tangents(uvmap = uvLayerTangent)
		_pesDebugLog('exportMeshGeometry: after calc_tangents')
		
		
		
		class Vertex:
			def __init__(self):
				self.position = None
				self.boneMapping = {}
				self.loops = []
		
		class Loop:
			def __init__(self):
				self.normal = None
				self.color = None
				self.uv = []
				
				self.tangents = []
				self.loopIndices = []
			
			def matches(self, other):
				if (self.color != None) != (other.color != None):
					return False
				if self.color != None and tuple(self.color) != tuple(other.color):
					return False
				if len(self.uv) != len(other.uv):
					return False
				for i in range(len(self.uv)):
					if self.uv[i].u != other.uv[i].u:
						return False
					if self.uv[i].v != other.uv[i].v:
						return False
				# Do an approximate check for normals.
				if self.normal.dot(other.normal) < 0.99:
					return False
				return True
			
			def add(self, other):
				self.tangents += other.tangents
				self.loopIndices += other.loopIndices
				self.normal = self.normal.slerp(other.normal, 1.0 / len(self.loopIndices))
			
			def computeTangent(self):
				# Filter out zero tangents
				nonzeroTangents = []
				for tangent in self.tangents:
					if tangent.length_squared > 0.1:
						nonzeroTangents.append(tangent)
				
				if len(nonzeroTangents) == 0:
					# Make up a tangent to avoid crashes
					# Cross product the loop normal with any vector that is not parallel with it.
					bestVector = None
					for v in [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)]:
						vector = mathutils.Vector(v)
						if bestVector == None or abs(vector.dot(self.normal)) < abs(bestVector.dot(self.normal)):
							bestVector = vector
					return bestVector.cross(self.normal)
				
				# Average out the different tangents.
				# In case of conflicts, bias towards the first entry in the list.
				averageTangent = nonzeroTangents[0]
				weight = 1
				remaining = nonzeroTangents[1:]
				while len(remaining) > 0:
					skipped = []
					for tangent in remaining:
						if averageTangent.dot(tangent) < -0.9:
							skipped.append(tangent)
						else:
							weight += 1
							averageTangent = averageTangent.slerp(tangent, 1.0 / weight)
					if len(skipped) == len(remaining):
						break
					remaining = skipped
				return averageTangent
		
		vertices = []
		for i in range(len(modifiedBlenderMesh.vertices)):
			blenderVertex = modifiedBlenderMesh.vertices[i]
			vertex = Vertex()
			vertex.position = FmdlFile.FmdlFile.Vector3(
				blenderVertex.co.x,
				blenderVertex.co.z,
				-blenderVertex.co.y,
			)
			for group in blenderVertex.groups:
				vertex.boneMapping[boneVector[group.group]] = group.weight
			vertices.append(vertex)
		
		for i in range(len(modifiedBlenderMesh.loops)):
			blenderLoop = modifiedBlenderMesh.loops[i]
			vertex = vertices[blenderLoop.vertex_index]
			
			loop = Loop()
			loop.normal = blenderLoop.normal
			loop.tangents = [blenderLoop.tangent]
			loop.loopIndices = [i]
			
			if colorLayer is not None:
				# Blender 4.x color_attributes already return RGBA (4 floats).
				# Older code appended an extra 1.0 (assuming RGB=3), producing 5
				# components and breaking pack('<4B', ...). Clamp/pad to exactly 4.
				rawColor = [c for c in modifiedBlenderMesh.color_attributes[colorLayer].data[i].color]
				loop.color = (rawColor + [1.0, 1.0, 1.0, 1.0])[0:4]
			loop.uv.append(FmdlFile.FmdlFile.Vector2(
				modifiedBlenderMesh.uv_layers[uvLayerColor].data[i].uv[0],
				1.0 - modifiedBlenderMesh.uv_layers[uvLayerColor].data[i].uv[1],
			))
			if uvLayerNormal != None:
				loop.uv.append(FmdlFile.FmdlFile.Vector2(
					modifiedBlenderMesh.uv_layers[uvLayerNormal].data[i].uv[0],
					1.0 - modifiedBlenderMesh.uv_layers[uvLayerNormal].data[i].uv[1],
				))
			
			found = False
			for otherLoop in vertex.loops:
				if otherLoop.matches(loop):
					otherLoop.add(loop)
					found = True
					break
			if not found:
				vertex.loops.append(loop)
		
		fmdlVertices = []
		fmdlLoopVertices = {}
		for vertex in vertices:
			for loop in vertex.loops:
				fmdlVertex = FmdlFile.FmdlFile.Vertex()
				fmdlVertex.position = vertex.position
				fmdlVertex.boneMapping = vertex.boneMapping
				fmdlVertex.normal = FmdlFile.FmdlFile.Vector4(
					loop.normal.x,
					loop.normal.z,
					-loop.normal.y,
					1.0,
				)
				tangent = loop.computeTangent()
				fmdlVertex.tangent = FmdlFile.FmdlFile.Vector4(
					tangent.x,
					tangent.z,
					-tangent.y,
					1.0,
				)
				fmdlVertex.color = loop.color
				fmdlVertex.uv = loop.uv
				fmdlVertices.append(fmdlVertex)
				for loopIndex in loop.loopIndices:
					fmdlLoopVertices[loopIndex] = fmdlVertex
		
		fmdlFaces = []
		for face in modifiedBlenderMesh.polygons:
			fmdlFaces.append(FmdlFile.FmdlFile.Face(
				fmdlLoopVertices[face.loop_start + 2],
				fmdlLoopVertices[face.loop_start + 1],
				fmdlLoopVertices[face.loop_start + 0],
			))
		
		_pesDebugLog('exportMeshGeometry: DONE verts=%d faces=%d' % (len(fmdlVertices), len(fmdlFaces)))
		bpy.data.meshes.remove(modifiedBlenderMesh)
		return (fmdlVertices, fmdlFaces)
	
	def exportMesh(blenderMeshObject, materialFmdlObjects, bonesByName, scene):
		blenderMesh = blenderMeshObject.data
		name = blenderMeshObject.name
		
		vertexFields = FmdlFile.FmdlFile.VertexFields()
		vertexFields.hasNormal = True
		vertexFields.hasTangent = True

		if len(blenderMesh.color_attributes) == 0:
			colorLayer = None
			vertexFields.hasColor = False
		elif len(blenderMesh.color_attributes) == 1:
			colorLayer = None
			vertexFields.hasColor = False
		elif len(blenderMesh.color_attributes) == 2:
			colorLayer = 1
			vertexFields.hasColor = True
		else:
			raise FmdlExportError("Mesh '%s' has more than one color layer." % name)
		
		materials = [material for material in blenderMesh.materials if material is not None]
		if len(materials) == 0:
			raise FmdlExportError("Mesh '%s' does not have an associated material." % name)
		if len(materials) > 1:
			raise FmdlExportError("Mesh '%s' has multiple associated materials, including '%s' and '%s'." % (name, materials[0].name, materials[1].name))
		blenderMaterial = materials[0]
		
		if len(blenderMesh.uv_layers) == 0:
			raise FmdlExportError("Mesh '%s' does not have a UV map." % name)
		elif len(blenderMesh.uv_layers) == 1:
			uvLayerColor = blenderMesh.uv_layers[0].name
			uvLayerNormal = None
			vertexFields.uvCount = 1
		else:
			colorUvMaps = []
			normalUvMaps = []
			for slot in blenderMaterial.fmdl_texture_slots:
				if slot == None:
					continue
				uvLayerName = slot.uv_layer
				if uvLayerName not in blenderMesh.uv_layers:
					continue
				if '_NRM' in slot.texture.fmdl_texture_role:
					uvMaps = normalUvMaps
				else:
					uvMaps = colorUvMaps
				if uvLayerName not in uvMaps:
					uvMaps.append(uvLayerName)
			
			if len(colorUvMaps) > 1:
				raise FmdlExportError("Mesh '%s' has ambiguous UV maps: multiple UV maps configured as primary UV map." % name)
			if len(normalUvMaps) > 1:
				raise FmdlExportError("Mesh '%s' has ambiguous UV maps: multiple UV maps configured as normals UV map." % name)
			
			if len(colorUvMaps) == 0 and 'UVMap' in blenderMesh.uv_layers and 'UVMap' not in normalUvMaps:
				colorUvMaps.append('UVMap')
			if len(normalUvMaps) == 0 and 'normal_map' in blenderMesh.uv_layers and 'normal_map' not in colorUvMaps:
				normalUvMaps.append('normal_map')
			if len(colorUvMaps) == 0 and len(normalUvMaps) == 1 and len(blenderMesh.uv_layers) == 2:
				for layer in blenderMesh.uv_layers:
					if layer.name != normalUvMaps[0]:
						colorUvMaps.append(layer.name)
						break
			
			if len(colorUvMaps) == 0:
				raise FmdlExportError("Mesh '%s' has ambiguous UV maps: found %s UV maps, but no primary UV map is configured." % (name, len(blenderMesh.uv_layers)))
			if len(normalUvMaps) == 0:
				raise FmdlExportError("Mesh '%s' has ambiguous UV maps: found %s UV maps, but no normals UV map is configured." % (name, len(blenderMesh.uv_layers)))
			
			uvLayerColor = colorUvMaps[0]
			if colorUvMaps[0] == normalUvMaps[0]:
				uvLayerNormal = None
				vertexFields.uvCount = 1
			else:
				uvLayerNormal = normalUvMaps[0]
				vertexFields.uvCount = 2
		
		boneVector = [bonesByName[vertexGroup.name] for vertexGroup in blenderMeshObject.vertex_groups]
		if len(boneVector) > 0:
			vertexFields.hasBoneMapping = True
		
		(vertices, faces) = exportMeshGeometry(blenderMeshObject, colorLayer, uvLayerColor, uvLayerNormal, boneVector, scene)
		
		mesh = FmdlFile.FmdlFile.Mesh()
		mesh.vertices = vertices
		mesh.faces = faces
		mesh.boneGroup = FmdlFile.FmdlFile.BoneGroup()
		mesh.boneGroup.bones = boneVector
		mesh.materialInstance = materialFmdlObjects[blenderMaterial]
		mesh.alphaEnum = blenderMesh.fmdl_alpha_enum
		mesh.shadowEnum = blenderMesh.fmdl_shadow_enum
		mesh.vertexFields = vertexFields
		
		return mesh
	
	def exportCustomBoundingBox(blenderMeshObject, fmdlMeshObject):
		latticeObject = None
		for child in blenderMeshObject.children:
			if child.type == 'LATTICE':
				if latticeObject is not None:
					raise FmdlExportError("Mesh '%s' has multiple conflicting custom bounding boxes." % blenderMeshObject.name)
				latticeObject = child
		
		if latticeObject is None:
			return None
		
		fmdlMeshObject.extensionHeaders.add("Custom-Bounding-Box-Meshes")
		
		transformedLattice = latticeObject.data.copy()
		transformedLattice.transform(latticeObject.matrix_world)
		transformedLatticeObject = bpy.data.objects.new('measurement', transformedLattice)
		boundingBox = transformedLatticeObject.bound_box
		boundingBoxFmdlNotation = [(boundingBox[i][0], boundingBox[i][2], -boundingBox[i][1]) for i in range(8)]
		minCoordinates = tuple(min([boundingBoxFmdlNotation[j][i] for j in range(8)]) for i in range(3))
		maxCoordinates = tuple(max([boundingBoxFmdlNotation[j][i] for j in range(8)]) for i in range(3))
		bpy.data.objects.remove(transformedLatticeObject)
		bpy.data.lattices.remove(transformedLattice)
		
		return FmdlFile.FmdlFile.BoundingBox(
			FmdlFile.FmdlFile.Vector4(
				minCoordinates[0],
				minCoordinates[1],
				minCoordinates[2],
				1.0
			),
			FmdlFile.FmdlFile.Vector4(
				maxCoordinates[0],
				maxCoordinates[1],
				maxCoordinates[2],
				1.0
			)
		)
	
	def determineParentBlenderObject(blenderObject, blenderRootObject, parentBlenderObjects):
		if blenderObject in parentBlenderObjects:
			return
		
		parentBlenderObject = blenderObject.parent
		while parentBlenderObject != None:
			if parentBlenderObject == blenderRootObject:
				parentBlenderObject = None
			elif parentBlenderObject.type in ['MESH', 'EMPTY']:
				break
			else:
				parentBlenderObject = parentBlenderObject.parent
		
		if parentBlenderObject != None:
			determineParentBlenderObject(parentBlenderObject, blenderRootObject, parentBlenderObjects)
		
		parentBlenderObjects[blenderObject] = parentBlenderObject
	
	def createMeshGroup(blenderObject, name, parentMeshGroup, meshGroups, meshGroupFmdlObjects):
		meshGroup = FmdlFile.FmdlFile.MeshGroup()
		meshGroup.name = name
		# Fill in meshGroup.boundingBox later
		meshGroup.visible = True
		
		if parentMeshGroup != None:
			meshGroup.parent = parentMeshGroup
			parentMeshGroup.children.append(meshGroup)
		
		meshGroups.append(meshGroup)
		meshGroupFmdlObjects[blenderObject] = meshGroup
		
		return meshGroup
	
	def exportMeshGroup(blenderObject, parentBlenderObjects, meshGroups, meshGroupFmdlObjects):
		if blenderObject in meshGroupFmdlObjects:
			return meshGroupFmdlObjects[blenderObject]
		
		if parentBlenderObjects[blenderObject] != None:
			parentMeshGroup = exportMeshGroup(parentBlenderObjects[blenderObject], parentBlenderObjects, meshGroups, meshGroupFmdlObjects)
		else:
			parentMeshGroup = None
		
		return createMeshGroup(blenderObject, blenderObject.name, parentMeshGroup, meshGroups, meshGroupFmdlObjects)
	
	def exportMeshMeshGroup(blenderMeshObject, meshFmdlObjects, parentBlenderObjects, meshGroups, meshGroupFmdlObjects):
		meshIdName = exportSettings.meshIdName
		if (
			    blenderMeshObject.name.startswith(meshIdName)
			and blenderMeshObject not in parentBlenderObjects.values()
		):
			if parentBlenderObjects[blenderMeshObject] is not None:
				parentMeshGroup = exportMeshGroup(parentBlenderObjects[blenderMeshObject], parentBlenderObjects, meshGroups, meshGroupFmdlObjects)
			else:
				parentMeshGroup = None
			meshGroup = createMeshGroup(blenderMeshObject, '', parentMeshGroup, meshGroups, meshGroupFmdlObjects)
		else:
			meshGroup = exportMeshGroup(blenderMeshObject, parentBlenderObjects, meshGroups, meshGroupFmdlObjects)
		
		meshGroup.meshes.append(meshFmdlObjects[blenderMeshObject])
	
	def exportMeshGroups(blenderMeshObjects, meshFmdlObjects, blenderRootObject):
		parentBlenderObjects = {}
		for blenderMeshObject in blenderMeshObjects:
			determineParentBlenderObject(blenderMeshObject, blenderRootObject, parentBlenderObjects)
		
		meshGroups = []
		meshGroupFmdlObjects = {}
		for blenderMeshObject in blenderMeshObjects:
			exportMeshMeshGroup(blenderMeshObject, meshFmdlObjects, parentBlenderObjects, meshGroups, meshGroupFmdlObjects)
		
		return meshGroups
	
	def sortMeshes(meshGroups):
		return [mesh for meshGroup in meshGroups for mesh in meshGroup.meshes]
	
	def calculateBoneBoundingBoxes(bones, meshes):
		boneVertexPositions = {}
		for bone in bones:
			boneVertexPositions[bone] = []
		
		for mesh in meshes:
			if not mesh.vertexFields.hasBoneMapping:
				continue
			
			for vertex in mesh.vertices:
				for bone in vertex.boneMapping:
					boneVertexPositions[bone].append(vertex.position)
		
		for bone in bones:
			vertexPositions = boneVertexPositions[bone]
			if len(vertexPositions) == 0:
				bone.boundingBox = FmdlFile.FmdlFile.BoundingBox(
					FmdlFile.FmdlFile.Vector4(0.0, 0.0, 0.0, 1.0),
					FmdlFile.FmdlFile.Vector4(0.0, 0.0, 0.0, 1.0)
				)
			else:
				bone.boundingBox = FmdlFile.FmdlFile.BoundingBox(
					FmdlFile.FmdlFile.Vector4(
						min(position.x for position in vertexPositions),
						min(position.y for position in vertexPositions),
						min(position.z for position in vertexPositions),
						1.0
					),
					FmdlFile.FmdlFile.Vector4(
						max(position.x for position in vertexPositions),
						max(position.y for position in vertexPositions),
						max(position.z for position in vertexPositions),
						1.0
					)
				)
	
	def calculateMeshBoundingBox(mesh, meshCustomBoundingBoxes):
		if mesh in meshCustomBoundingBoxes:
			return meshCustomBoundingBoxes[mesh]
		
		vertices = mesh.vertices
		if len(vertices) == 0:
			return None
		
		return FmdlFile.FmdlFile.BoundingBox(
			FmdlFile.FmdlFile.Vector4(
				min(vertex.position.x for vertex in vertices),
				min(vertex.position.y for vertex in vertices),
				min(vertex.position.z for vertex in vertices),
				1.0
			),
			FmdlFile.FmdlFile.Vector4(
				max(vertex.position.x for vertex in vertices),
				max(vertex.position.y for vertex in vertices),
				max(vertex.position.z for vertex in vertices),
				1.0
			)
		)
	
	def calculateMeshGroupBoundingBox(meshGroup, meshCustomBoundingBoxes):
		boundingBoxes = []
		for mesh in meshGroup.meshes:
			boundingBox = calculateMeshBoundingBox(mesh, meshCustomBoundingBoxes)
			if boundingBox != None:
				boundingBoxes.append(boundingBox)
		for child in meshGroup.children:
			boundingBox = calculateMeshGroupBoundingBox(child, meshCustomBoundingBoxes)
			if boundingBox != None:
				boundingBoxes.append(boundingBox)
		
		if len(boundingBoxes) == 0:
			meshGroup.boundingBox = FmdlFile.FmdlFile.BoundingBox(
				FmdlFile.FmdlFile.Vector4(0.0, 0.0, 0.0, 1.0),
				FmdlFile.FmdlFile.Vector4(0.0, 0.0, 0.0, 1.0)
			)
			return None
		
		meshGroup.boundingBox = FmdlFile.FmdlFile.BoundingBox(
			FmdlFile.FmdlFile.Vector4(
				min(box.min.x for box in boundingBoxes),
				min(box.min.y for box in boundingBoxes),
				min(box.min.z for box in boundingBoxes),
				1.0
			),
			FmdlFile.FmdlFile.Vector4(
				max(box.max.x for box in boundingBoxes),
				max(box.max.y for box in boundingBoxes),
				max(box.max.z for box in boundingBoxes),
				1.0
			)
		)
		
		return meshGroup.boundingBox
	
	def calculateBoundingBoxes(meshGroups, bones, meshes, meshCustomBoundingBoxes):
		calculateBoneBoundingBoxes(bones, meshes)
		
		for meshGroup in meshGroups:
			if meshGroup.parent == None:
				calculateMeshGroupBoundingBox(meshGroup, meshCustomBoundingBoxes)
	
	def listMeshObjects(context, rootObjectName):
		if rootObjectName != None and rootObjectName not in context.scene.objects:
			rootObjectName = None
		
		if rootObjectName == None:
			blenderMeshObjects = []
			blenderArmatureObjects = []
			for object in context.scene.objects:
				if object.type == 'MESH' and len(object.data.polygons) > 0:
					blenderMeshObjects.append(object)
					for modifier in object.modifiers:
						if modifier.type == 'ARMATURE':
							blenderArmatureObject = modifier.object
							if blenderArmatureObject not in blenderArmatureObjects:
								blenderArmatureObjects.append(blenderArmatureObject)
			if (
				len(blenderArmatureObjects) == 1 and
				blenderArmatureObjects[0].parent != None and
				blenderArmatureObjects[0].parent.parent == None
			):
				blenderRootObject = blenderArmatureObjects[0].parent
			else:
				blenderRootObject = None
		else:
			blenderRootObject = context.scene.objects[rootObjectName]
			blenderMeshObjects = []
			
			def findMeshObjects(blenderObject, blenderMeshObjects):
				if blenderObject.type == 'MESH' and len(blenderObject.data.polygons) > 0:
					blenderMeshObjects.append(blenderObject)
				for child in blenderObject.children:
					findMeshObjects(child, blenderMeshObjects)
			findMeshObjects(blenderRootObject, blenderMeshObjects)
			
			if blenderRootObject.type == 'MESH' and len(blenderRootObject.data.polygons) > 0:
				blenderRootObject = blenderRootObject.parent
		
		return (blenderMeshObjects, blenderRootObject)
	
	
	
	if exportSettings == None:
		exportSettings = ExportSettings()
	
	if context.mode != 'OBJECT':
		bpy.ops.object.mode_set(mode = 'OBJECT')
	
	
	
	(blenderMeshObjects, blenderRootObject) = listMeshObjects(context, rootObjectName)
	_pesDebugLog('exportFmdl: listMeshObjects OK count=%d' % len(blenderMeshObjects))
	
	(materialInstances, materialFmdlObjects) = exportMaterials(blenderMeshObjects)
	_pesDebugLog('exportFmdl: exportMaterials OK')
	
	(bones, bonesByName) = exportBones(blenderMeshObjects)
	_pesDebugLog('exportFmdl: exportBones OK count=%d' % len(bones))
	
	meshFmdlObjects = {}
	meshCustomBoundingBoxes = {}
	for blenderMeshObject in blenderMeshObjects:
		_pesDebugLog('exportFmdl: exportMesh %s' % blenderMeshObject.name)
		mesh = exportMesh(blenderMeshObject, materialFmdlObjects, bonesByName, context.scene)
		meshFmdlObjects[blenderMeshObject] = mesh
		
		boundingBox = exportCustomBoundingBox(blenderMeshObject, mesh)
		if boundingBox is not None:
			meshCustomBoundingBoxes[mesh] = boundingBox
	
	meshGroups = exportMeshGroups(blenderMeshObjects, meshFmdlObjects, blenderRootObject)
	
	meshes = sortMeshes(meshGroups)
	
	calculateBoundingBoxes(meshGroups, bones, meshes, meshCustomBoundingBoxes)
	
	fmdlFile = FmdlFile.FmdlFile()
	fmdlFile.bones = bones
	fmdlFile.materialInstances = materialInstances
	fmdlFile.meshes = meshes
	fmdlFile.meshGroups = meshGroups
	
	_pesDebugLog('exportFmdl: meshes built, before encoding extensions')
	if exportSettings.enableExtensions and exportSettings.enableVertexLoopPreservation:
		fmdlFile = FmdlSplitVertexEncoding.encodeFmdlVertexLoopPreservation(fmdlFile)
	_pesDebugLog('exportFmdl: after vertex loop preservation')
	if exportSettings.enableExtensions and exportSettings.enableMeshSplitting:
		fmdlFile = FmdlMeshSplitting.encodeFmdlSplitMeshes(fmdlFile)
	_pesDebugLog('exportFmdl: after mesh splitting')
	
	errors = []
	for mesh in fmdlFile.meshes:
		meshName = None
		for meshGroup in fmdlFile.meshGroups:
			if len(meshGroup.meshes) == 1 and meshGroup.meshes[0] == mesh:
				if meshGroup.name != "":
					meshName = meshGroup.name
				break
		if meshName is None:
			meshIndex = fmdlFile.meshes.index(mesh)
			meshIdName = exportSettings.meshIdName
			meshName = "{0}_{1}".format(meshIdName, meshIndex)
		
		if len(mesh.vertices) > 65535:
			errors.append("Mesh '%s' contains %s vertices out of a maximum of 65535" % (meshName, len(mesh.vertices)))
		if len(mesh.faces) > 21845:
			errors.append("Mesh '%s' contains %s faces out of a maximum of 21845" % (meshName, len(mesh.faces)))
		if mesh.boneGroup is not None and len(mesh.boneGroup.bones) > 32:
			errors.append("Mesh '%s' bone group contains %s bones out of a maximum of 32" % (meshName, len(mesh.boneGroup.bones)))
	if len(errors) > 0:
		raise FmdlExportError(errors)
	
	return fmdlFile

def exportSummary(context, rootObjectName):
	def objectName(blenderObject, rootObject):
		name = blenderObject.name
		parent = blenderObject.parent
		while parent is not None and parent != rootObject:
			name = "%s/%s" % (parent.name, name)
			parent = parent.parent
		return name
	
	def materialSummary(material):
		output = "\tMaterial %s:\n" % material.name
		output += "\t\tshader \"%s\"\n" % material.fmdl_material_shader
		output += "\t\ttechnique \"%s\"\n" % material.fmdl_material_technique
		for parameter in material.fmdl_material_parameters:
			output += "\t\tparameter [%s] = [%.2f, %.2f, %.2f, %.2f]\n" % (parameter.name, *parameter.parameters)
		for slot in material.fmdl_texture_slots:
			if slot == None:
				continue
			output += "\t\ttexture [%s] = \n" % slot.texture.fmdl_texture_role
			output += "\t\t\t\"%s\"\n" % slot.texture.fmdl_texture_directory
			output += "\t\t\t\t\"%s\"\n" % slot.texture.fmdl_texture_filename
		return output
	
	def skeletonSummary(armature):
		bodyPartAllBones = {}
		for pesVersion in PesSkeletonData.skeletonBones:
			for bodyPart in PesSkeletonData.skeletonBones[pesVersion]:
				if bodyPart not in bodyPartAllBones:
					bodyPartAllBones[bodyPart] = set()
				bodyPartAllBones[bodyPart].update(PesSkeletonData.skeletonBones[pesVersion][bodyPart])
		bodyPartUniqueBones = {}
		for bodyPart in bodyPartAllBones:
			bodyPartUniqueBones[bodyPart] = bodyPartAllBones[bodyPart].copy()
			for otherBodyPart in bodyPartAllBones:
				if otherBodyPart != bodyPart:
					bodyPartUniqueBones[bodyPart].difference_update(bodyPartAllBones[otherBodyPart])
		
		bones = sorted([bone.name for bone in armature.bones])
		requiredBodyParts = set()
		for bone in bones:
			for bodyPart in bodyPartUniqueBones:
				if bone in bodyPartUniqueBones[bodyPart]:
					requiredBodyParts.add(bodyPart)
		
		bodyPartVersions = {}
		unknownBones = []
		for bone in bones:
			selectedBodyPart = None
			for bodyPart in requiredBodyParts:
				if bone in bodyPartAllBones[bodyPart]:
					selectedBodyPart = bodyPart
					break
			if selectedBodyPart is None:
				for bodyPart in sorted(list(bodyPartAllBones.keys()), reverse=True):
					if bone in bodyPartAllBones[bodyPart]:
						selectedBodyPart = bodyPart
						break
			
			if selectedBodyPart is None:
				unknownBones.append(bone)
			else:
				for pesVersion in PesSkeletonData.skeletonBones:
					if bone in PesSkeletonData.skeletonBones[pesVersion][selectedBodyPart]:
						minimumPesVersion = pesVersion
						break
				# minimumPesVersion MUST be set at this point
				if selectedBodyPart not in bodyPartVersions or bodyPartVersions[selectedBodyPart] < minimumPesVersion:
					bodyPartVersions[selectedBodyPart] = minimumPesVersion
		
		output = ""
		if len(bodyPartVersions) == 0 and len(unknownBones) == 0:
			output += "\tSkeleton: none\n"
		elif len(bodyPartVersions) == 1 and len(unknownBones) == 0:
			bodyPart = list(bodyPartVersions.keys())[0]
			output += "\tSkeleton: %s %s\n" % (bodyPartVersions[bodyPart], bodyPart)
		else:
			output += "\tSkeleton:\n"
			for bodyPart in sorted(list(bodyPartVersions.keys())):
				output += "\t\tFound bones for %s %s\n" % (bodyPartVersions[bodyPart], bodyPart)
			if len(unknownBones) > 0:
				chunks = [[]]
				for bone in unknownBones:
					if len(chunks[-1]) >= 6:
						chunks.append([])
					chunks[-1].append('"%s"' % bone)
				output += "\t\tFound unknown bones:\n"
				for boneChunk in chunks:
					output += "\t\t\t%s\n" % ", ".join(boneChunk)
		return output
	
	def meshSummary(blenderMeshObject, rootObject):
		mesh = blenderMeshObject.data
		lattices = [child for child in blenderMeshObject.children if child.type == 'LATTICE']
		armatures = [modifier.object.data for modifier in blenderMeshObject.modifiers if modifier.type == 'ARMATURE']
		
		output = "Exporting mesh %s\n" % objectName(blenderMeshObject, rootObject)
		output += "\tVertices: %s\n" % len(mesh.vertices)
		output += "\tFaces: %s\n" % len(mesh.polygons)
		output += "\tAlpha Enum: %s\n" % mesh.fmdl_alpha_enum
		output += "\tShadow Enum: %s\n" % mesh.fmdl_shadow_enum
		if len(mesh.color_attributes) == 1:
			output += "\tMesh has vertex color information\n"
		elif len(mesh.color_attributes) > 1:
			output += "\tMesh has inconsistent vertex color information\n"
		if len(lattices) == 1:
			output += "\tMesh has custom bounding box\n"
		elif len(lattices) > 1:
			output += "\tMesh has inconsistent bounding box\n"
		if len(mesh.materials) == 0:
			output += "\tMaterial: none\n"
		elif len(mesh.materials) == 1:
			output += materialSummary(mesh.materials[0])
		else:
			output += "\tMaterial: inconsistent\n"
		if len(armatures) == 0:
			output += "\tSkeleton: none\n"
		elif len(armatures) == 1:
			output += skeletonSummary(armatures[0])
		else:
			output += "\tSkeleton: inconsistent\n"
		return output
	
	meshObjects = {}
	if rootObjectName is None:
		rootObject = None
		output = "Export summary\n"
		for blenderObject in context.scene.objects:
			if blenderObject.type == 'MESH' and len(blenderObject.data.polygons) > 0:
				meshObjects[objectName(blenderObject, rootObject)] = blenderObject
	else:
		rootObject = bpy.data.objects[rootObjectName]
		output = "Export summary for %s\n" % objectName(rootObject, None)
		def findMeshObjects(blenderObject):
			if blenderObject.type == 'MESH' and len(blenderObject.data.polygons) > 0:
				meshObjects[objectName(blenderObject, rootObject)] = blenderObject
			for child in blenderObject.children:
				findMeshObjects(child)
		findMeshObjects(rootObject)
	output += "------------------------------\n"

	for key in sorted(list(meshObjects.keys())):
		output += meshSummary(meshObjects[key], rootObject)
	return output
