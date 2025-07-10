from pathlib import Path
from mutagen.easyid3 import EasyID3
from mutagen.mp3 import MP3
import shutil
import os
import time
import re
from collections import defaultdict

# Ruta base montada desde host
raw_dir = Path("/music/spottube")
organized_dir = Path("/music")
playlist_base = organized_dir / "playlist"
playlist_base.mkdir(parents=True, exist_ok=True)

mp3_files_by_folder = defaultdict(list)

# Recolectar archivos MP3
for mp3_file in raw_dir.rglob("*.mp3"):
    if mp3_file.is_file():
        mp3_files_by_folder[mp3_file.parent].append(mp3_file)

# Procesar cada carpeta
for folder, files in mp3_files_by_folder.items():
    artists = set()
    albums = set()
    playlist_entries = []

    for mp3_file in files:
        try:
            audio = MP3(mp3_file, ID3=EasyID3)
            artist = sanitize(audio.get("artist", ["Unknown Artist"])[0])
            album = sanitize(audio.get("album", ["Unknown Album"])[0])
            title = sanitize(audio.get("title", [mp3_file.stem])[0])

            dest_path = organized_dir / artist / album
            dest_path.mkdir(parents=True, exist_ok=True)

            new_file_path = dest_path / f"{title}.mp3"
            if not new_file_path.exists():
                shutil.copy2(mp3_file, new_file_path)

            playlist_entries.append(str(new_file_path))
            artists.add(artist)
            albums.add(album)
        except Exception as e:
            print(f"Error procesando {mp3_file}: {e}")

    if len(artists) > 1 or len(albums) > 1:
        playlist_path = playlist_base / f"{folder.name}.m3u8"
        with open(playlist_path, "w", encoding="utf-8") as f:
            for path in playlist_entries:
                abs_path = Path(path).resolve()
                try:
                    idx = abs_path.parts.index("music")
                    rel_path = "/" + "/".join(abs_path.parts[idx:])
                    f.write(rel_path + "\n")
                except ValueError:
                    print(f"Ruta {abs_path} no contiene 'music', saltando.")

# 🔄 Limpieza: eliminar carpetas si TODOS los archivos son mayores a 1 día
for subfolder in raw_dir.iterdir():
    if subfolder.is_dir():
        mp3s = list(subfolder.glob("*.mp3"))
        if not mp3s:
            continue
        if all((time.time() - f.stat().st_mtime) > 300 for f in mp3s):
            try:
                shutil.rmtree(subfolder)
                print(f"🧹 Carpeta eliminada: {subfolder}")
            except Exception as e:
                print(f"❌ Error eliminando {subfolder}: {e}")