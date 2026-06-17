import io
import struct
import zlib

class DecodeError(Exception):
	pass

#
# Pixel formats:
# (ftex format ID) -- (dds dxgiFormat)
#
#  0 -- D3DFMT_A8R8G8B8
#  1 -- DXGI_FORMAT_R8_UNORM
#  2 -- BC1U ["DXT1"]
#  3 -- BC2U ["DXT3"]
#  4 -- BC3U ["DXT5"]
#  8 -- BC4U [DXGI_FORMAT_BC4_UNORM]
#  9 -- BC5U [DXGI_FORMAT_BC5_UNORM]
# 10 -- BC6H_UF16 [DXGI_FORMAT_BC6H_UF16]
# 11 -- BC7U [DXGI_FORMAT_BC7_UNORM]
# 12 -- DXGI_FORMAT_R16G16B16A16_FLOAT
# 13 -- DXGI_FORMAT_R32G32B32A32_FLOAT
# 14 -- DXGI_FORMAT_R10G10B10A2_UNORM
# 15 -- DXGI_FORMAT_R11G11B10_FLOAT
#
# Format support:
#  PES18: 0-4
#  PES19: 0-4, 8-15
#

#
# For each ftex format, stores the height and width of encoded blocks,
# and the size in bytes of each encoded block.
#
formatBlockConfiguration = {
	0:  (1,  4), # D3DFMT_A8R8G8B8
	1:  (1,  1), # DXGI_FORMAT_R8_UNORM
	2:  (4,  8), # DXGI_FORMAT_BC1_UNORM ["DXT1"]
	3:  (4, 16), # DXGI_FORMAT_BC2_UNORM ["DXT3"]
	4:  (4, 16), # DXGI_FORMAT_BC3_UNORM ["DXT5"]
	8:  (4,  8), # DXGI_FORMAT_BC4_UNORM
	9:  (4, 16), # DXGI_FORMAT_BC5_UNORM
	10: (4, 16), # DXGI_FORMAT_BC6H_UF16
	11: (4, 16), # DXGI_FORMAT_BC7_UNORM
	12: (1,  8), # DXGI_FORMAT_R16G16B16A16_FLOAT
	13: (1, 16), # DXGI_FORMAT_R32G32B32A32_FLOAT
	14: (1,  4), # DXGI_FORMAT_R10G10B10A2_UNORM
	15: (1,  4), # DXGI_FORMAT_R11G11B10_FLOAT
}
def ddsMipmapSize(ftexFormat, width, height, depth, mipmapIndex):
	(blockSizePixels, blockSizeBytes) = formatBlockConfiguration[ftexFormat]
	scaleFactor = 2 ** mipmapIndex
	
	mipmapWidth = (width + scaleFactor - 1) // scaleFactor
	mipmapHeight = (height + scaleFactor - 1) // scaleFactor
	mipmapDepth = (depth + scaleFactor - 1) // scaleFactor
	
	widthBlocks = (mipmapWidth + blockSizePixels - 1) // blockSizePixels
	heightBlocks = (mipmapHeight + blockSizePixels - 1) // blockSizePixels
	return widthBlocks * heightBlocks * mipmapDepth * blockSizeBytes

def ftexToDdsBuffer(ftexBuffer):
	def readImageBuffer(stream, imageOffset, chunkCount, uncompressedSize, compressedSize):
		stream.seek(imageOffset, 0)
		
		if chunkCount == 0:
			if compressedSize == 0:
				uncompressedBuffer = bytearray(uncompressedSize)
				if stream.readinto(uncompressedBuffer) != len(uncompressedBuffer):
					raise DecodeError("Unexpected end of stream")
				return uncompressedBuffer
			else:
				compressedBuffer = bytearray(compressedSize)
				if stream.readinto(compressedBuffer) != len(compressedBuffer):
					raise DecodeError("Unexpected end of stream")
				# When the compressed size is not smaller than the uncompressed size
				# the data is actually stored raw. Also fall back to raw if zlib fails.
				if uncompressedSize > 0 and compressedSize >= uncompressedSize:
					return bytes(compressedBuffer)
				try:
					return zlib.decompress(compressedBuffer)
				except Exception:
					return bytes(compressedBuffer)
		
		chunks = []
		for i in range(chunkCount):
			header = bytearray(8)
			if stream.readinto(header) != len(header):
				raise DecodeError("Incomplete chunk header")
			(
				chunkCompressedSize,
				chunkUncompressedSize,
				offset,
			) = struct.unpack('< HH I', header)
			# Compression detection: some FTEX writers flag an uncompressed chunk via
			# the high bit of the offset, but many (including PES19 BC5 normal maps)
			# leave it unset and simply store the chunk raw whenever the compressed
			# size is not smaller than the uncompressed size (typically the smallest
			# mipmaps). Treat both cases as uncompressed.
			highBitUncompressed = (offset & (1 << 31)) != 0
			offset &= ~(1 << 31)
			isCompressed = (not highBitUncompressed) and (chunkCompressedSize < chunkUncompressedSize)
			
			chunks.append((offset, chunkCompressedSize, isCompressed))

		imageBuffers = []
		for (offset, chunkCompressedSize, isCompressed) in chunks:
			stream.seek(imageOffset + offset, 0)
			compressedBuffer = bytearray(chunkCompressedSize)
			if stream.readinto(compressedBuffer) != len(compressedBuffer):
				raise DecodeError("Unexpected end of stream")
			if isCompressed:
				try:
					decompressedBuffer = zlib.decompress(compressedBuffer)
				except Exception:
					# Not valid zlib after all: use the raw bytes instead of aborting
					# the whole texture decode.
					decompressedBuffer = bytes(compressedBuffer)
			else:
				decompressedBuffer = bytes(compressedBuffer)
			imageBuffers.append(decompressedBuffer)
		return b''.join(imageBuffers)
	
	
	
	inputStream = io.BytesIO(ftexBuffer)
	
	header = bytearray(64)
	if inputStream.readinto(header) != len(header):
		raise DecodeError("Incomplete ftex header")
	
	(
		ftexMagic,
		ftexVersion,
		ftexPixelFormat,
		ftexWidth,
		ftexHeight,
		ftexDepth,
		ftexMipmapCount,
		ftexNrt,
		ftexFlags,
		ftexUnknown1,
		ftexUnknown2,
		ftexTextureType,
		ftexFtexsCount,
		ftexUnknown3,
		ftexHash1,
		ftexHash2,
	) = struct.unpack('< 4s f HHHH  BB HIII  BB 14x  8s 8s', header)
	
	if ftexMagic != b'FTEX':
		raise DecodeError("Incorrect ftex signature")
	
	if ftexVersion < 2.025:
		raise DecodeError("Unsupported ftex version")
	if ftexVersion > 2.045:
		raise DecodeError("Unsupported ftex version")
	if ftexFtexsCount > 0:
		raise DecodeError("Unsupported ftex variant")
	if ftexMipmapCount == 0:
		raise DecodeError("Unsupported ftex variant")
	
	
	
	ddsFlags = (
		  0x1        # capabilities
		| 0x2        # height
		| 0x4        # width
		| 0x1000     # pixel format
	)
	ddsCapabilities1 = 0x1000 # texture
	ddsCapabilities2 = 0
	
	if (ftexTextureType & 4) != 0:
		# Cube map, with six faces
		if ftexDepth > 1:
			raise DecodeError("Unsupported ftex variant")
		imageCount = 6
		ddsDepth = 1
		ddsCapabilities1 |= 0x8    # complex
		ddsCapabilities2 |= 0xfe00 # cube map with six faces
		
		ddsExtensionDimension = 3 # 2D
		ddsExtensionFlags = 0x4 # cube map
	elif ftexDepth > 1:
		# Volume texture
		imageCount = 1
		ddsDepth = ftexDepth
		ddsFlags |= 0x800000      # depth
		ddsCapabilities2 |= 0x200000 # volume texture
		
		ddsExtensionDimension = 4 # 3D
		ddsExtensionFlags = 0
	else:
		# Regular 2D texture
		imageCount = 1
		ddsDepth = 1
		
		ddsExtensionDimension = 3 # 2D
		ddsExtensionFlags = 0
	
	ddsMipmapCount = ftexMipmapCount
	mipmapCount = ftexMipmapCount
	ddsFlags |= 0x20000          # mipmapCount
	ddsCapabilities1 |= 0x8      # complex
	ddsCapabilities1 |= 0x400000 # mipmap
	
	
	
	#
	# A frame is a byte array containing a single mipmap element of a single image.
	# Cube maps have six images with mipmaps, and so 6 * $mipmapCount frames.
	# Other textures just have $mipmapCount frames.
	#
	frameSpecifications = []
	for i in range(imageCount):
		for j in range(mipmapCount):
			mipmapHeader = bytearray(16)
			if inputStream.readinto(mipmapHeader) != len(mipmapHeader):
				raise DecodeError("Incomplete mipmap header")
			(
				offset,
				uncompressedSize,
				compressedSize,
				index,
				ftexsNumber,
				chunkCount,
			) = struct.unpack('< I I I BB H', mipmapHeader)
			if index != j:
				raise DecodeError("Unexpected mipmap")
			
			expectedFrameSize = ddsMipmapSize(ftexPixelFormat, ftexWidth, ftexHeight, ddsDepth, j)
			frameSpecifications.append((offset, chunkCount, uncompressedSize, compressedSize, expectedFrameSize))
	
	frames = []
	for (offset, chunkCount, uncompressedSize, compressedSize, expectedSize) in frameSpecifications:
		frame = readImageBuffer(inputStream, offset, chunkCount, uncompressedSize, compressedSize)
		if len(frame) < expectedSize:
			frame += bytes(expectedSize - len(frame))
		elif len(frame) > expectedSize:
			frame = frame[0:expectedSize]
		frames.append(frame)
	
	
	
	ddsPitch = None
	if ftexPixelFormat == 0:
		ddsPitchOrLinearSize = 4 * ftexWidth
		ddsFlags |= 0x8 # pitch
		useExtensionHeader = False
		
		ddsFormatFlags = 0x41 # uncompressed rgba
		ddsFourCC = b'\0\0\0\0'
		ddsRgbBitCount = 32
		ddsRBitMask = 0x00ff0000
		ddsGBitMask = 0x0000ff00
		ddsBBitMask = 0x000000ff
		ddsABitMask = 0xff000000
	else:
		ddsPitchOrLinearSize = len(frames[0])
		ddsFlags |= 0x80000 # linear size
		
		ddsFormatFlags = 0x4 # compressed
		ddsRgbBitCount = 0
		ddsRBitMask = 0
		ddsGBitMask = 0
		ddsBBitMask = 0
		ddsABitMask = 0
		
		ddsFourCC = None
		ddsExtensionFormat = None
		
		if ftexPixelFormat == 1:
			ddsExtensionFormat = 61 # DXGI_FORMAT_R8_UNORM
		elif ftexPixelFormat == 2:
			ddsFourCC = b'DXT1'
		elif ftexPixelFormat == 3:
			ddsFourCC = b'DXT3'
		elif ftexPixelFormat == 4:
			ddsFourCC = b'DXT5'
		elif ftexPixelFormat == 8:
			ddsExtensionFormat = 80 # DXGI_FORMAT_BC4_UNORM
		elif ftexPixelFormat == 9:
			ddsExtensionFormat = 83 # DXGI_FORMAT_BC5_UNORM
		elif ftexPixelFormat == 10:
			ddsExtensionFormat = 95 # DXGI_FORMAT_BC6H_UF16
		elif ftexPixelFormat == 11:
			ddsExtensionFormat = 98 # DXGI_FORMAT_BC7_UNORM
		elif ftexPixelFormat == 12:
			ddsExtensionFormat = 10 # DXGI_FORMAT_R16G16B16A16_FLOAT
		elif ftexPixelFormat == 13:
			ddsExtensionFormat = 2  # DXGI_FORMAT_R32G32B32A32_FLOAT
		elif ftexPixelFormat == 14:
			ddsExtensionFormat = 24 # DXGI_FORMAT_R10G10B10A2_UNORM
		elif ftexPixelFormat == 15:
			ddsExtensionFormat = 26 # DXGI_FORMAT_R11G11B10_FLOAT
		else:
			raise DecodeError("Unsupported ftex codec")
		
		if ddsExtensionFormat is not None:
			ddsFourCC = b'DX10'
			useExtensionHeader = True
		else:
			useExtensionHeader = False
	
	
	
	outputStream = io.BytesIO()
	outputStream.write(struct.pack('< 4s 7I 44x 2I 4s 5I 2I 12x',
		b'DDS ',
		
		124, # header size
		ddsFlags,
		ftexHeight,
		ftexWidth,
		ddsPitchOrLinearSize,
		ddsDepth,
		ddsMipmapCount,
		
		32, # substructure size
		ddsFormatFlags,
		ddsFourCC,
		ddsRgbBitCount,
		ddsRBitMask,
		ddsGBitMask,
		ddsBBitMask,
		ddsABitMask,
		
		ddsCapabilities1,
		ddsCapabilities2,
	))
	
	if useExtensionHeader:
		outputStream.write(struct.pack('< 5I',
			ddsExtensionFormat,
			ddsExtensionDimension,
			ddsExtensionFlags,
			1, # array size
			0, # flags
		))
	
	for frame in frames:
		outputStream.write(frame)
	
	return outputStream.getbuffer()

def ftexToDds(ftexFilename, ddsFilename):
	inputStream = open(ftexFilename, 'rb')
	inputBuffer = inputStream.read()
	inputStream.close()
	
	outputBuffer = ftexToDdsBuffer(inputBuffer)
	
	outputStream = open(ddsFilename, 'wb')
	outputStream.write(outputBuffer)
	outputStream.close()



def ddsToFtexBuffer(ddsBuffer, colorSpace):
	def encodeImage(data):
		chunkSize = 1 << 14 # Value known not to crash PES
		chunkCount = (len(data) + chunkSize - 1) // chunkSize
		
		headerBuffer = bytearray()
		chunkBuffer = bytearray()
		chunkBufferOffset = chunkCount * 8
		
		for i in range(chunkCount):
			chunk = data[chunkSize * i : chunkSize * (i + 1)]
			compressedChunk = zlib.compress(chunk, level = 9)
			offset = len(chunkBuffer)
			chunkBuffer += compressedChunk
			headerBuffer += struct.pack('< HHI',
				len(compressedChunk),
				len(chunk),
				offset + chunkBufferOffset,
			)
		
		return (headerBuffer + chunkBuffer, chunkCount)
	
	inputStream = io.BytesIO(ddsBuffer)
	
	header = bytearray(128)
	if inputStream.readinto(header) != len(header):
		raise DecodeError("Incomplete dds header")
	
	(
		ddsMagic,
		ddsHeaderSize,
		ddsFlags,
		ddsHeight,
		ddsWidth,
		ddsPitchOrLinearSize,
		ddsDepth,
		ddsMipmapCount,
		# ddsReserved,
		
		ddsPixelFormatSize,
		ddsFormatFlags,
		ddsFourCC,
		ddsRgbBitCount,
		ddsRBitMask,
		ddsGBitMask,
		ddsBBitMask,
		ddsABitMask,
		
		ddsCapabilities1,
		ddsCapabilities2,
		# ddsReserved,
	) = struct.unpack('< 4s 7I 44x 2I 4s 5I 2I 12x', header)
	
	if ddsMagic != b'DDS ':
		raise DecodeError("Incorrect dds signature")
	if ddsHeaderSize != 124:
		raise DecodeError("Incorrect dds header")
	
	if (
		    (ddsCapabilities1 & 0x400000) > 0 # mipmap
		and (ddsMipmapCount > 1)
	):
		mipmapCount = ddsMipmapCount
	else:
		mipmapCount = 1
	
	if ddsCapabilities2 & 0x200 > 0: #cubemap
		if ddsCapabilities2 & 0xfe00 != 0xfe00:
			raise DecodeError("Incomplete dds cube maps not supported")
		isCubeMap = True
		cubeEntries = 6
	else:
		isCubeMap = False
		cubeEntries = 1
	
	if ddsCapabilities2 & 0x200000 > 0: # volume texture
		depth = ddsDepth
	else:
		depth = 1
	
	if isCubeMap and depth > 1:
		raise DecodeError("Invalid dds combination: cube map and volume map both set")
	
	if colorSpace == 'LINEAR':
		ftexTextureType = 0x1
	elif colorSpace == 'SRGB':
		ftexTextureType = 0x3
	elif colorSpace == 'NORMAL':
		ftexTextureType = 0x9
	else:
		ftexTextureType = 0x9
	if isCubeMap:
		ftexTextureType |= 0x4
	
	
	
	if ddsFormatFlags & 0x4 == 0: # fourCC absent
		if (
			    (ddsFormatFlags & 0x40) > 0 # rgb
			and (ddsFormatFlags & 0x1) > 0  # alpha
			and ddsRBitMask == 0x00ff0000
			and ddsGBitMask == 0x0000ff00
			and ddsBBitMask == 0x000000ff
			and ddsABitMask == 0xff000000
		):
			ftexPixelFormat = 0
		else:
			raise DecodeError("Unsupported dds codec")
	elif ddsFourCC == b'DX10':
		extensionHeader = bytearray(20)
		if inputStream.readinto(extensionHeader) != len(extensionHeader):
			raise DecodeError("Incomplete dds extension header")
		
		(
			ddsExtensionFormat,
			# ddsOther,
		) = struct.unpack('< I 16x', extensionHeader)
		
		if ddsExtensionFormat == 61: # DXGI_FORMAT_R8_UNORM
			ftexPixelFormat = 1
		elif ddsExtensionFormat == 71: # DXGI_FORMAT_BC1_UNORM ["DXT1"]
			ftexPixelFormat = 2
		elif ddsExtensionFormat == 74: # DXGI_FORMAT_BC2_UNORM ["DXT3"]
			ftexPixelFormat = 3
		elif ddsExtensionFormat == 77: # DXGI_FORMAT_BC3_UNORM ["DXT5"]
			ftexPixelFormat = 4
		elif ddsExtensionFormat == 80: # DXGI_FORMAT_BC4_UNORM
			ftexPixelFormat = 8
		elif ddsExtensionFormat == 83: # DXGI_FORMAT_BC5_UNORM
			ftexPixelFormat = 9
		elif ddsExtensionFormat == 95: # DXGI_FORMAT_BC6H_UF16
			ftexPixelFormat = 10
		elif ddsExtensionFormat == 98: # DXGI_FORMAT_BC7_UNORM
			ftexPixelFormat = 11
		elif ddsExtensionFormat == 10: # DXGI_FORMAT_R16G16B16A16_FLOAT
			ftexPixelFormat = 12
		elif ddsExtensionFormat == 1:  # DXGI_FORMAT_R32G32B32A32_FLOAT
			ftexPixelFormat = 13
		elif ddsExtensionFormat == 24: # DXGI_FORMAT_R10G10B10A2_UNORM
			ftexPixelFormat = 14
		elif ddsExtensionFormat == 26: # DXGI_FORMAT_R11G11B10_FLOAT
			ftexPixelFormat = 15
		else:
			raise DecodeError("Unsupported dds codec")
	elif ddsFourCC == b'8888':
		ftexPixelFormat = 0
	elif ddsFourCC == b'DXT1':
		ftexPixelFormat = 2
	elif ddsFourCC == b'DXT3':
		ftexPixelFormat = 3
	elif ddsFourCC == b'DXT5':
		ftexPixelFormat = 4
	else:
		raise DecodeError("Unsupported dds codec")
	
	if ftexPixelFormat > 4:
		ftexVersion = 2.04
	else:
		ftexVersion = 2.03
	
	
	
	frameBuffer = bytearray()
	mipmapEntries = []
	for _ in range(cubeEntries):
		for mipmapIndex in range(mipmapCount):
			length = ddsMipmapSize(ftexPixelFormat, ddsWidth, ddsHeight, depth, mipmapIndex)
			frame = inputStream.read(length)
			if len(frame) != length:
				raise DecodeError("Unexpected end of dds stream")
			
			frameOffset = len(frameBuffer)
			(compressedFrame, chunkCount) = encodeImage(frame)
			frameBuffer += compressedFrame
			mipmapEntries.append((frameOffset, len(frame), len(compressedFrame), mipmapIndex, chunkCount))
	
	mipmapBuffer = bytearray()
	mipmapBufferOffset = 64
	frameBufferOffset = mipmapBufferOffset + len(mipmapEntries) * 16
	for (relativeFrameOffset, uncompressedSize, compressedSize, mipmapIndex, chunkCount) in mipmapEntries:
		mipmapBuffer += struct.pack('< III BB H',
			relativeFrameOffset + frameBufferOffset,
			uncompressedSize,
			compressedSize,
			mipmapIndex,
			0, # ftexs number
			chunkCount
		)
	
	header = struct.pack('< 4s f HHHH  BB HIII  BB 14x  16x',
		b'FTEX',
		ftexVersion,
		ftexPixelFormat,
		ddsWidth,
		ddsHeight,
		depth,
		mipmapCount,
		0x02, # nrt flag, meaning unknown
		0x11, # unknown flags
		1, # unknown
		0, # unknown
		ftexTextureType,
		0, # ftexs count
		0, # unknown
		# 14 bytes padding
		# 16 bytes hashes
	)
	
	return header + mipmapBuffer + frameBuffer

def ddsToFtex(ddsFilename, ftexFilename, colorSpace):
	inputStream = open(ddsFilename, 'rb')
	inputBuffer = inputStream.read()
	inputStream.close()
	
	outputBuffer = ddsToFtexBuffer(inputBuffer, colorSpace)
	
	outputStream = open(ftexFilename, 'wb')
	outputStream.write(outputBuffer)
	outputStream.close()


# ---------------------------------------------------------------------------
# Pure-Python BCn texture decoding + PNG export.
#
# Fix for FTEX/DDS textures rendering grey/colourless in Blender 2.8+/4.x/5.x:
#  * Blender's built-in DDS loader is unreliable across versions.
#  * The add-on referenced Ftex.blenderImageLoadFtex(), which never existed.
# We now decode the texels ourselves (BC1/BC2/BC3/BC4/BC5 + uncompressed) and
# hand Blender a plain PNG, which always loads with correct colour. Unsupported
# formats (BC6H/BC7) fall back to letting Blender try the raw .dds.
# Only depends on struct + zlib (already imported), so it runs inside Blender.
# ---------------------------------------------------------------------------

def _bcnColor565(c):
    r = (c >> 11) & 0x1F
    g = (c >> 5) & 0x3F
    b = c & 0x1F
    r = (r << 3) | (r >> 2)
    g = (g << 2) | (g >> 4)
    b = (b << 3) | (b >> 2)
    return (r, g, b)

def _bcnDecodeBc1Colors(block):
    c0, c1 = struct.unpack_from('<HH', block, 0)
    bits = struct.unpack_from('<I', block, 4)[0]
    r0, g0, b0 = _bcnColor565(c0)
    r1, g1, b1 = _bcnColor565(c1)
    col = [(r0, g0, b0, 255), (r1, g1, b1, 255), None, None]
    if c0 > c1:
        col[2] = ((2*r0+r1)//3, (2*g0+g1)//3, (2*b0+b1)//3, 255)
        col[3] = ((r0+2*r1)//3, (g0+2*g1)//3, (b0+2*b1)//3, 255)
    else:
        col[2] = ((r0+r1)//2, (g0+g1)//2, (b0+b1)//2, 255)
        col[3] = (0, 0, 0, 0)
    return [col[(bits >> (2*i)) & 0x3] for i in range(16)]

def _bcnDecodeBc4Block(block, off):
    a0, a1 = block[off], block[off+1]
    idxbits = int.from_bytes(block[off+2:off+8], 'little')
    if a0 > a1:
        a = [a0, a1, (6*a0+1*a1)//7, (5*a0+2*a1)//7, (4*a0+3*a1)//7,
             (3*a0+4*a1)//7, (2*a0+5*a1)//7, (1*a0+6*a1)//7]
    else:
        a = [a0, a1, (4*a0+1*a1)//5, (3*a0+2*a1)//5, (2*a0+3*a1)//5,
             (1*a0+4*a1)//5, 0, 255]
    return [a[(idxbits >> (3*i)) & 0x7] for i in range(16)]

def _bcnPutBlock(rgba, w, h, bx, by, pixels):
    for py in range(4):
        y = by*4 + py
        if y >= h:
            continue
        for px in range(4):
            x = bx*4 + px
            if x >= w:
                continue
            r, g, b, a = pixels[py*4 + px]
            o = (y*w + x) * 4
            rgba[o] = r; rgba[o+1] = g; rgba[o+2] = b; rgba[o+3] = a

def _bcnDecodeBlocks(data, w, h, blockbytes, decode_fn):
    rgba = bytearray(w*h*4)
    bw = (w + 3)//4
    bh = (h + 3)//4
    off = 0
    for by in range(bh):
        for bx in range(bw):
            block = data[off:off+blockbytes]
            off += blockbytes
            _bcnPutBlock(rgba, w, h, bx, by, decode_fn(block))
    return rgba

def _bcnBc1(block):
    return _bcnDecodeBc1Colors(block)

def _bcnBc2(block):
    colors = _bcnDecodeBc1Colors(block[8:16])
    out = []
    for i in range(16):
        nib = (block[i//2] >> (4*(i & 1))) & 0xF
        a = (nib << 4) | nib
        r, g, b, _ = colors[i]
        out.append((r, g, b, a))
    return out

def _bcnBc3(block):
    alpha = _bcnDecodeBc4Block(block, 0)
    colors = _bcnDecodeBc1Colors(block[8:16])
    return [(colors[i][0], colors[i][1], colors[i][2], alpha[i]) for i in range(16)]

def _bcnBc4(block):
    vals = _bcnDecodeBc4Block(block, 0)
    return [(v, v, v, 255) for v in vals]

def _bcnBc5(block):
    rr = _bcnDecodeBc4Block(block, 0)
    gg = _bcnDecodeBc4Block(block, 8)
    out = []
    for i in range(16):
        r = rr[i]; g = gg[i]
        nx = (r/127.5)-1.0; ny = (g/127.5)-1.0
        nz2 = 1.0 - nx*nx - ny*ny
        nz = (nz2**0.5) if nz2 > 0 else 0.0
        out.append((r, g, int((nz*0.5+0.5)*255), 255))
    return out

def decodeDdsToRgba(dds):
    if dds[:4] != b'DDS ':
        raise ValueError('not a DDS buffer')
    h = struct.unpack_from('<I', dds, 12)[0]
    w = struct.unpack_from('<I', dds, 16)[0]
    pf_flags = struct.unpack_from('<I', dds, 80)[0]
    fourcc = dds[84:88]
    data_off = 128
    fmt = None
    if fourcc == b'DX10':
        dxgi = struct.unpack_from('<I', dds, 128)[0]
        data_off = 148
        dxgi_map = {70:'BC1',71:'BC1',72:'BC1',73:'BC2',74:'BC2',75:'BC2',
                    76:'BC3',77:'BC3',78:'BC3',79:'BC4',80:'BC4',81:'BC4',
                    82:'BC5',83:'BC5',84:'BC5'}
        fmt = dxgi_map.get(dxgi)
        if fmt is None:
            raise ValueError('unsupported dxgiFormat %d' % dxgi)
    elif fourcc == b'DXT1':
        fmt = 'BC1'
    elif fourcc == b'DXT3':
        fmt = 'BC2'
    elif fourcc == b'DXT5':
        fmt = 'BC3'
    elif fourcc in (b'BC4U', b'ATI1'):
        fmt = 'BC4'
    elif fourcc in (b'BC5U', b'ATI2'):
        fmt = 'BC5'
    elif (pf_flags & 0x40):
        bitcount = struct.unpack_from('<I', dds, 88)[0]
        rmask, gmask, bmask, amask = struct.unpack_from('<IIII', dds, 92)
        bpp = bitcount//8
        data = dds[data_off:data_off + w*h*bpp]
        rgba = bytearray(w*h*4)
        def _sh(mask):
            if mask == 0: return 0
            s = 0; m = mask
            while not (m & 1):
                m >>= 1; s += 1
            return s
        rs = _sh(rmask); gs = _sh(gmask); bs = _sh(bmask); as_ = _sh(amask)
        for i in range(w*h):
            px = int.from_bytes(data[i*bpp:i*bpp+bpp], 'little')
            rgba[i*4] = (px & rmask) >> rs if rmask else 0
            rgba[i*4+1] = (px & gmask) >> gs if gmask else 0
            rgba[i*4+2] = (px & bmask) >> bs if bmask else 0
            rgba[i*4+3] = ((px & amask) >> as_) if amask else 255
        return w, h, rgba, 'UNCOMPRESSED'
    else:
        raise ValueError('unsupported fourCC %r' % fourcc)
    bb = 8 if fmt in ('BC1', 'BC4') else 16
    fn = {'BC1': _bcnBc1, 'BC2': _bcnBc2, 'BC3': _bcnBc3, 'BC4': _bcnBc4, 'BC5': _bcnBc5}[fmt]
    return w, h, _bcnDecodeBlocks(dds[data_off:], w, h, bb, fn), fmt

def writePng(path, w, h, rgba):
    def chunk(typ, data):
        c = struct.pack('>I', len(data)) + typ + data
        c += struct.pack('>I', zlib.crc32(typ + data) & 0xffffffff)
        return c
    raw = bytearray()
    stride = w*4
    for y in range(h):
        raw.append(0)
        raw += rgba[y*stride:(y+1)*stride]
    out = b'\x89PNG\r\n\x1a\n'
    out += chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0))
    out += chunk(b'IDAT', zlib.compress(bytes(raw), 6))
    out += chunk(b'IEND', b'')
    with open(path, 'wb') as f:
        f.write(out)

def _bcnSafeName(image):
    base = image.name
    for ch in '\\/:*?"<>|':
        base = base.replace(ch, '_')
    return base or 'pes_tex'

def _bcnAvgRgb(rgba, w, h):
    step = max(1, (w*h)//4096)
    r = g = b = n = 0
    total = w*h
    i = 0
    while i < total:
        o = i*4
        r += rgba[o]; g += rgba[o+1]; b += rgba[o+2]; n += 1
        i += step
    if n == 0:
        return (0, 0, 0)
    return (r//n, g//n, b//n)

def _bcnLoadDdsBufferIntoImage(image, ddsBuffer, tempDir, log=None):
    import os
    colorspace = None
    try:
        colorspace = image.colorspace_settings.name
    except Exception:
        colorspace = None
    try:
        w, h, rgba, fmt = decodeDdsToRgba(ddsBuffer)
        if log:
            try:
                log("bcn decode: name=%s fmt=%s size=%dx%d avgRGB=%s" % (
                    getattr(image, 'name', '?'), fmt, w, h, _bcnAvgRgb(rgba, w, h)))
            except Exception:
                pass
        pngPath = os.path.join(tempDir, _bcnSafeName(image) + '.png')
        writePng(pngPath, w, h, rgba)
        image.filepath = pngPath
        image.source = 'FILE'
        image.reload()
    except Exception as e:
        # Unsupported format (BC6H/BC7) or decode error: let Blender try the raw dds.
        if log:
            try:
                log("bcn decode FAILED (%s); falling back to raw dds for %s" % (e, getattr(image, 'name', '?')))
            except Exception:
                pass
        try:
            tmpDds = os.path.join(tempDir, _bcnSafeName(image) + '.dds')
            with open(tmpDds, 'wb') as f:
                f.write(ddsBuffer)
            image.filepath = tmpDds
            image.source = 'FILE'
            image.reload()
        except Exception:
            return False
    # Restore the role-based colour space (reload may reset it to sRGB).
    if colorspace is not None:
        try:
            image.colorspace_settings.name = colorspace
        except Exception:
            pass
    try:
        return tuple(image.size)[0] > 0
    except Exception:
        return False

def blenderImageLoadFtex(image, tempDir, log=None):
    import bpy, os
    path = bpy.path.abspath(image.filepath) if image.filepath else ''
    if not path or not os.path.isfile(path):
        return False
    with open(path, 'rb') as f:
        ddsBuffer = bytes(ftexToDdsBuffer(f.read()))
    return _bcnLoadDdsBufferIntoImage(image, ddsBuffer, tempDir, log)

def blenderImageLoadDds(image, tempDir, log=None):
    import bpy, os
    path = bpy.path.abspath(image.filepath) if image.filepath else ''
    if not path or not os.path.isfile(path):
        return False
    with open(path, 'rb') as f:
        ddsBuffer = f.read()
    return _bcnLoadDdsBufferIntoImage(image, ddsBuffer, tempDir, log)

def blenderImageLoadAny(image, path, tempDir, log=None):
    import bpy, os
    low = (path or '').lower()
    image.filepath = path
    if low.endswith('.ftex'):
        return blenderImageLoadFtex(image, tempDir, log)
    if low.endswith('.dds'):
        return blenderImageLoadDds(image, tempDir, log)
    image.source = 'FILE'
    try:
        if os.path.isfile(path):
            image.reload()
    except Exception:
        pass
    try:
        return tuple(image.size)[0] > 0
    except Exception:
        return False
