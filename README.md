# -
简单视频兼容转码工具

## 运行
python -m video_compat_converter.py

## 打包
python -m PyInstaller ^
  --noconfirm ^
  --onefile ^
  --windowed ^
  --name 视频兼容转码工具 ^
  --add-binary "ffmpeg.exe;." ^
  --add-binary "ffprobe.exe;." ^
  video_compat_converter.py