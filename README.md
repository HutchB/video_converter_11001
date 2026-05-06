简单视频兼容转码工具
📖 简介
简单视频兼容转码工具是一个基于 Python 和 FFmpeg 的图形化视频转换工具，可以将各种格式的视频批量转换为 H.264 + AAC 编码的 MP4 文件，确保在内网老旧电脑上也能正常播放。
🔧 运行方式
方式一：直接运行 Python 脚本
bash
python -m video_compat_converter.py
或

bash
python video_compat_converter.py
方式二：打包为独立 EXE 文件
使用 PyInstaller 打包成单个可执行文件，无需安装 Python 环境即可运行。

完整打包命令
bash
python -m PyInstaller ^
  --noconfirm ^
  --onefile ^
  --windowed ^
  --name 视频兼容转码工具 ^
  --add-binary "ffmpeg.exe;." ^
  --add-binary "ffprobe.exe;." ^
  video_compat_converter.py
