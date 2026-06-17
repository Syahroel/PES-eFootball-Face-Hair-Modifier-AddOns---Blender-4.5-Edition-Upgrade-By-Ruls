# PES / eFootball Face & Hair Modifier — Blender 4.5+ Edition

> Add-on Blender untuk **mengimpor & mengekspor file `.fmdl`** (face, hair, oral) dari Pro Evolution Soccer / eFootball PES 2021, lengkap dengan konversi tekstur **FTEX** otomatis.
>
> Versi ini adalah hasil **porting dari Blender 2.92 ke Blender 4.5 LTS (dan ke atas)**, dengan banyak perbaikan kompatibilitas API, material, dan tekstur.

---

## 📌 Kredit (Credits)

| Peran | Nama |
|---|---|
| Base awal | **the4chancup** community |
| Modified by | **Ruls Base** |
| FMDL tooling asli | **MjTs140914 / MjTs** |
| Repository asli | https://github.com/MjTs140914/PES_Face_Hair_Modifier |
| Port & perbaikan Blender 4.5+ | *(edisi ini)* |
| Lisensi | **MIT** |

Addon asli dirancang **hanya untuk Blender 2.79 / 2.92**. Edisi ini membuatnya berjalan di **Blender 4.5 LTS ke atas** tanpa kehilangan fungsi aslinya.

---

## 🎯 Apa Ini?

Addon ini menambahkan panel **"eFootball PES2021 Face/Hair Modifier"** di Blender yang memungkinkan kamu:

- **Import** model wajah (`face_high.fmdl`), rambut (`hair_high.fmdl`), dan mulut/oral (`oral.fmdl`) PES ke Blender.
- **Export** kembali ke format `.fmdl` setelah diedit.
- **Konversi tekstur FTEX** PES (`.ftex`) ↔ `.dds` secara otomatis, lalu menampilkannya di material Blender.
- **Ekstrak `face.fpk`** untuk mengeluarkan model & tekstur.
- Membangun **node material otomatis** (kulit, rambut, mata, transparansi) agar model langsung terlihat wajar di viewport Blender 4.5 (EEVEE Next / Cycles).

---

## 💻 Persyaratan (Requirements)

| Komponen | Keterangan |
|---|---|
| **Blender** | **4.5 LTS atau lebih baru** (diuji di 4.5.10 LTS) |
| **OS** | Windows (disarankan). Ekstrak FPK & konversi format DDS memanggil `.exe` di folder `Gzs`. |
| **Folder `Gzs`** | **Wajib ada** di `Data/Gzs/`. Berisi `GzsTool.exe`, `FtexTools.exe`, `texconv.exe`, `Settings.ini`, `PesFoxShader.xml`, `icons/`, `base_file.blend`. |
| **Python** | Sudah termasuk di dalam Blender (tidak perlu instal terpisah). |

> ⚠️ Untuk Blender 2.79 / 2.92 gunakan addon **versi asli**. Edisi ini khusus 4.5+.

---

## 📦 Instalasi

### Cara 1 — Install sebagai ZIP (disarankan)
1. Unduh/siapkan addon dalam bentuk `.zip` (berisi `PES_Face_Hair_Modifier.py` + folder `Data/`).
2. Buka Blender → **Edit ▸ Preferences ▸ Add-ons**.
3. Klik **Install...**, pilih file `.zip`.
4. Centang kotak addon **"eFootball PES2021 Face/Hair Modifier"** untuk mengaktifkannya.

### Cara 2 — Copy manual ke folder addons
1. Salin folder addon ke direktori scripts/addons Blender, contoh:
   ```
   C:\Users\<NAMA>\AppData\Roaming\Blender Foundation\Blender\4.5\scripts\addons\
   ```
2. Buka Blender → **Edit ▸ Preferences ▸ Add-ons** → aktifkan addon-nya.

### ⚠️ PENTING saat MEMPERBARUI file addon
Blender memuat kode Python addon ke **memori (RAM) hanya sekali saat dibuka**. Jika kamu mengganti file `.py` (misal `Data/IO.py`):

1. **Tutup Blender sepenuhnya**, lalu buka lagi — **ATAU** matikan & nyalakan kembali addon di Preferences (reload).
2. Hapus folder `__pycache__` di dalam folder `Data/` bila ada.
3. **Import ulang** model — node material hanya dibangun **saat impor**, jadi model lama di scene tidak ikut berubah.

> Mengganti `.py` lalu hanya import ulang **tanpa** restart = Blender tetap menjalankan kode lama.

---

## 🚀 Cara Pakai (Usage)

Panel addon muncul di **Properties ▸ Scene ▸ "eFootball PES2021 Face/Hair Modifier"** (atau di sidebar, tergantung versi).

### Mengimpor wajah
1. Klik **Import FACE** → pilih file `face_high.fmdl`.
2. Addon otomatis:
   - Membaca geometri, tulang (armature), dan UV.
   - Mencari & memuat tekstur (`.ftex`/`.dds`) dari folder yang sama dan folder induk (`sourceimages/#windx11`, `Common`, dll).
   - Membangun node material (kulit, rambut, mata, dll).

### Mengimpor rambut & oral
- **Import HAIR** → pilih `hair_high.fmdl`. Centang **Use Same Folder** untuk memakai folder yang sama dengan face.
- **Import ORAL** → pilih `oral.fmdl` (mulut/gigi/lidah).

### Mengekspor kembali
- Setelah edit, gunakan **Export FACE / Export HAIR / Export ORAL** untuk menulis kembali ke `.fmdl`.
- Pastikan setiap mesh hanya punya **satu material** (aturan FMDL).

### Ekstrak FACE.FPK
- Klik **Extract Face.Fpk** untuk mengeluarkan isi `.fpk` (model + tekstur).
- *(Menggunakan `GzsTool.exe` di folder `Gzs`; membutuhkan Windows.)*

### Import Settings / Export Settings
Menu dropdown untuk opsi lanjutan:
- **Mesh splitting**, **vertex/loop preservation** (ekstensi format),
- **Import bounding boxes**, **load textures** (matikan jika hanya butuh geometri).

---

## 🎨 Bagaimana Addon Menangani Tekstur FTEX

Ini adalah bagian yang **paling banyak berubah** dibanding versi asli. Ada **dua jalur konversi** yang bekerja bersama:

### Jalur 1 — Konverter Python Mandiri (`Ftex.py`) — untuk tampil di Blender

Di edisi ini, fungsi `Ftex.blenderImageLoadAny()` dipakai saat **import model** untuk memuat `.ftex`/`.dds` langsung ke gambar Blender **tanpa memanggil `.exe` apa pun**. Ini bekerja secara *cross-platform* dan tidak membutuhkan folder `Gzs`.

Cara kerjanya secara detail:

**Langkah 1 — `ftexToDds` / `ftexToDdsBuffer`**
- Membaca *header* FTEX: format piksel, lebar, tinggi, jumlah mipmap.
- Mengekstrak blok data piksel mentah dari file.
- Membungkus ulang menjadi file/buffer **DDS** standar yang bisa dibaca Blender.
- Kode format piksel yang didukung:

  | Kode | Format | Keterangan |
  |---|---|---|
  | 0 | RGBA8 | Tanpa kompresi |
  | 2 | BC1 / DXT1 | Kompresi lossy, tanpa alpha |
  | 3 | BC2 / DXT3 | Kompresi + alpha sharp |
  | 4 | BC3 / DXT5 | Kompresi + alpha smooth (paling umum) |
  | 8 | BC4U | Satu channel (grayscale) |
  | 9 | BC5U | Dua channel (normal map XY) |
  | 10 | BC6H | HDR |
  | 11 | BC7U | Kualitas tinggi |

**Langkah 2 — `decodeDdsToRgba`**
- Bila Blender tidak bisa membaca DDS secara langsung, modul men-*decode* blok terkompresi menjadi array **RGBA mentah** (piksel biasa).
- Untuk **BC5 / normal map** dua-channel: channel X dan Y di-*remap*, lalu Z direkonstruksi dengan rumus `Z = sqrt(1 − X² − Y²)` agar normal map tampil benar.

**Langkah 3 — `blenderImageLoadAny`**
- Pembungkus cerdas yang dipanggil `IO.py` saat impor:
  1. Coba muat file DDS langsung ke `bpy.data.images`.
  2. Kalau gagal, decode ke RGBA lalu masukkan pikselnya secara langsung ke objek gambar Blender.
  3. Fallback terakhir: tulis PNG sementara lalu muat dari disk.
- Selama proses ini, **tidak ada `.exe` yang dipanggil**.

**Pencarian tekstur rekursif** (`findTextureRecursive`):
- Bila tekstur tidak ada di folder utama, addon menelusuri folder-folder induk secara rekursif sampai menemukannya.
- Ini menyelesaikan masalah bulu mata/alis yang teksturnya ada di `Common/` atau folder lain di luar `#windx11`.

---

### Jalur 2 — Tools Eksternal di folder `Gzs` — untuk ekstrak & pack

Folder `Gzs` **masih wajib ada** dan dipakai untuk operasi di luar import/display:

| Operasi | Tool di `Gzs` | Keterangan |
|---|---|---|
| **Ekstrak FACE.FPK** | `GzsTool.exe` | Membuka isi file `.fpk` (model + tekstur) |
| **Export / repack ke .fpk** | `GzsTool.exe` | Mengemas kembali model yang telah diedit |
| **Konversi format DDS** (misal ke BC7) | `texconv.exe` | Mengonversi DDS ke format tertentu untuk ekspor |
| **Fallback decode FTEX** | `FtexTools.exe` | Cadangan bila `Ftex.py` gagal decode |
| Definisi shader, ikon UI, base scene | `PesFoxShader.xml`, `icons/`, `base_file.blend`, `Settings.ini` | Untuk panel UI dan export |

### Ringkasan jalur konversi

```
File .ftex/.dds
       │
       ├── [IMPORT / TAMPIL DI BLENDER]
       │         Ftex.py (Python murni) — TIDAK butuh Gzs
       │         └─ ftexToDds → decode BC1–BC5 → blenderImageLoadAny
       │
       └── [EKSTRAK FPK / EXPORT / KONVERSI DDS]
                 Gzs/*.exe (Windows) — WAJIB ada folder Gzs
                 └─ GzsTool.exe, texconv.exe, FtexTools.exe
```

---

## 🗨️ Bagaimana Addon Membangun Material

Saat impor (jika *load textures* aktif), addon membaca slot tekstur FMDL dan membangun node **Principled BSDF** dengan logika berikut:

### Pemilihan tekstur base color
- Tekstur **Base / `_SRGB`** dipakai sebagai warna dasar.
- Tekstur berikut **diabaikan** sebagai base color (karena bukan warna): `coeff`, `nrm`/`norm` (normal), `srm`/`spec` (specular/roughness), `msk`/`mask`, dan **`occl` (occlusion)**.
- Jika base tidak ketemu, addon mencari **tekstur warna pertama yang masuk akal** sebagai cadangan, lalu melakukan **pencarian rekursif** ke folder induk.

### Klasifikasi material
| Jenis | Dikenali dari nama | Perlakuan |
|---|---|---|
| **Kulit / Wajah** (`skin`, `head`, `face`, `shell`) | Roughness matte, **subsurface lembut radius kecil**, specular rendah | agar terlihat seperti kulit, bukan logam mengilap |
| **Rambut** (`hair`) | Specular sedang, roughness sedang, alpha cutout | helai rambut tembus pandang wajar |
| **Bola mata** (`eyeball`, `cornea`, `sclera`, `iris`, `eye`) | **Subsurface dimatikan**, glossy/basah, opaque | mencegah "mata merah menyala" |
| **Overlay mata** (`lash`, `eyebrow`, `eyeline`, `eyeshadow`, `eyelid`, `occlusion`) | Cutout gelap | bulu mata/alis tipis tidak jadi putih |
| **Transparan** (hair, eye, oral, glass) | Alpha aktif (HASHED/BLENDED) | bagian tembus pandang |

### Normal map
- Channel direkonstruksi dengan benar (X/Y di-remap, Z dihitung `sqrt(1 - x² - y²)`), termasuk normal **BC5** dua-channel.

---

## 🛠️ Changelog — Perbaikan Kompatibilitas Blender 4.5+

Versi ini memuat perbaikan menyeluruh dibanding addon 2.92 asli:

### Kompatibilitas API & crash
- ✅ Port pemanggilan API Blender 2.79/2.92 → **4.5 LTS** (struktur node, properti material, operator).
- ✅ Perbaikan beberapa **error registrasi & impor** (Error #1–#6) saat addon dimuat.
- ✅ Perbaikan **crash saat Export**.
- ✅ Perbaikan **regresi saat Import**.
- ✅ Perbaikan **crash pada ikon "friend"/UI** addon.

### Material & shading
- ✅ Perbaikan **"wajah abu-abu"** (specular wash) — kulit kini matte dengan specular rendah.
- ✅ Perbaikan **kulit pemain hitam** & **wajah hitam pemain putih** (penanganan alpha BSM yang benar; alpha mask tidak lagi menghitamkan tengah wajah).
- ✅ Perbaikan **rambut terlalu mengilap** → roughness matte + lantai roughness.
- ✅ Perbaikan **sheen rambut**.
- ✅ Perbaikan decode **`face_nrm` BC5** (normal map dua-channel).
- ✅ Perbaikan **rekonstruksi sumbu Z** pada normal rambut.

### Tekstur
- ✅ **Konverter FTEX Python mandiri** (`Ftex.py`) — import & tampil di Blender tidak lagi membutuhkan `FtexTools.exe` atau `texconv.exe`.
- ✅ **Pencarian tekstur rekursif** — addon menelusuri folder induk bila tekstur tak ada di folder yang sama.
- ✅ Perbaikan **bulu mata putih** — dulu karena tekstur tak ditemukan → fallback putih; kini fallback gelap yang benar + pencari rekursif.
- ✅ Decode FTEX **BC1–BC5** yang andal, termasuk pembacaan raw-chunk.
- ✅ Tekstur **occlusion tidak lagi dipakai** sebagai warna dasar material.

### Mata (Eye)
- ✅ Perbaikan **"mata merah menyala"**:
  - Bola mata **tidak lagi diklasifikasikan sebagai kulit** (tidak kena subsurface merah).
  - **Radius subsurface kulit dikecilkan** ke skala milimeter (mencegah bagian kecil/cekung menyala merah).
  - **Pass perbaikan mata pasca-impor** berbasis nama objek (`eyeL`/`eyeR`) — mematikan subsurface & memberi tampilan bola mata mengilap.
  - Tekstur **occlusion (`occl`) tidak lagi dipakai sebagai warna dasar** mata.

---

## 🧰 Troubleshooting

### "Saya sudah ganti `IO.py` tapi tidak ada perubahan"
Blender menyimpan kode addon di RAM. **Tutup Blender → buka lagi**, hapus `__pycache__`, lalu **import ulang** model. (Lihat bagian *Instalasi ▸ saat memperbarui*.)

### "Perubahan material tidak muncul di model yang sudah ada"
Node material dibangun **saat impor**. Hapus model lama → **import ulang**.

### Membuka System Console (untuk melihat log)
- Menu **Window ▸ Toggle System Console** (Windows). Jendela hitam berisi log akan terbuka.
- Berguna untuk melihat pesan seperti:
  ```
  eye fixup: object=eyeL -> subsurface killed, glossy eye applied
  addTexture: role=Base_SRGB FOUND=C:\...\face_bsm_alp.dds
  ```

### Peringatan `register_class ... does not contain *MT*/*UL*/*PT*`
Ini **kosmetik** dan tidak memengaruhi fungsi. Boleh diabaikan.

### Leher/kerah putih
**Normal**, bukan bug — itu bagian model dasar.

---

## ⚠️ Batasan yang Diketahui (Known Issues)

### Mata abu-abu / putih polos (tanpa iris)
Jika folder hasil ekstrak **tidak memuat tekstur warna mata asli** (iris/sclera) dan hanya ada `eye_occlusion_alp`, bola mata akan tampil **abu-abu/putih netral** (bukan merah). Ini **bukan bug addon** — file teksturnya memang tidak ada di folder tersebut.

**Solusi:** Cari & sertakan tekstur warna mata aslinya (sering berada di **FPK lain / Common textures**, bernama seperti `eye_l_*`, `iris`, `sclera`, `eye_col`). Tool seperti *Real Face Viewer* dapat menampilkan mata dengan benar karena mengambil tekstur dari lokasi tambahan tersebut.

### Hanya Windows untuk konversi DDS & ekstrak FPK
`texconv.exe`, `GzsTool.exe`, dan `FtexTools.exe` di folder `Gzs` hanya untuk Windows. **Namun**, fungsi **import & tampil tekstur** di Blender menggunakan konverter Python mandiri (`Ftex.py`) yang tidak bergantung platform.

### Tidak bisa menjalankan Blender headless di sandbox
Pengujian visual harus dilakukan di Blender desktop kamu.

---

## 📁 Struktur File

```
PES_Face_Hair_Modifier/
├── PES_Face_Hair_Modifier.py    # Entry point addon (panel UI, operator)
├── README.md                    # Dokumen ini
└── Data/
    ├── IO.py                    # Import/Export FMDL + pembangun node material
    ├── Ftex.py                  # Decoder/encoder tekstur FTEX ↔ DDS/PNG (BC1–BC5)
    ├── FmdlFile.py              # Parser format FMDL
    ├── FmdlMeshSplitting.py     # Ekstensi: pemecahan mesh
    ├── FmdlSplitVertexEncoding.py # Ekstensi: preservasi vertex/loop
    ├── PesFoxShader.py          # Definisi shader Fox Engine
    ├── PesSkeletonData.py       # Data tulang/skeleton PES
    ├── TiNA.py                  # Utilitas pendukung
    └── Gzs/                     # WAJIB ADA — tools eksternal Windows
        ├── GzsTool.exe          # Ekstrak & pack .fpk
        ├── FtexTools.exe        # Fallback konversi FTEX
        ├── texconv.exe          # Konversi format DDS
        ├── PesFoxShader.xml     # Definisi shader untuk export
        ├── Settings.ini         # Pengaturan addon
        ├── base_file.blend      # Scene dasar untuk export
        └── icons/               # Ikon UI panel
```

---

## ❓ FAQ

**T: Apakah bisa untuk Blender 4.5 ke atas?**  
J: Ya. Edisi ini memang dibuat untuk Blender 4.5 LTS+. Untuk 2.79/2.92 pakai addon asli.

**T: Apakah folder `Gzs` masih dibutuhkan?**  
J: **Ya, masih wajib ada.** Folder `Gzs` dipakai untuk ekstrak/pack `.fpk`, konversi DDS ke format game, dan sebagai fallback. Yang sudah **tidak butuh Gzs** hanyalah tampil tekstur saat import — itu sudah ditangani `Ftex.py` secara mandiri.

**T: Kenapa wajah sempat abu-abu / hitam / mengilap?**  
J: Itu masalah penanganan material/tekstur dari port lama, dan sudah diperbaiki di edisi ini (lihat Changelog).

**T: Kenapa mata saya merah?**  
J: Karena tekstur occlusion dipakai sebagai warna mata. Sudah diperbaiki — kini di-skip. Jika mata jadi abu-abu, berarti tekstur warna mata asli tidak ada di folder (lihat Known Issues).

**T: Saya ganti kode tapi tidak berubah.**  
J: Restart Blender + import ulang (lihat Troubleshooting).

---

## 📜 Lisensi

Dirilis di bawah lisensi **MIT**, mengikuti repository asli MjTs140914. Hormati kredit pembuat asli (the4chancup, Ruls Base, MjTs) saat mendistribusikan ulang.

---

*README ini dibuat untuk edisi Blender 4.5+ dari PES Face & Hair Modifier. Jika menemukan model dengan material yang belum tertangani, sertakan file `.fmdl` + teksturnya agar logika material dapat ditingkatkan.*
