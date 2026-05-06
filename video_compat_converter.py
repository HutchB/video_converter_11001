import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import date
from tkinter import filedialog, messagebox, ttk

try:
    import customtkinter as ctk
except ModuleNotFoundError as exc:
    raise SystemExit(
        "缺少 customtkinter 依赖，请在当前 Python 环境执行：\n"
        "python -m pip install customtkinter\n"
        "如果仍然报错，请先执行：python -c \"import sys; print(sys.executable)\" 确认 Python 路径。"
    ) from exc


TARGET_VIDEO_CODEC = "H.264 / AVC (libx264 baseline, yuv420p)"
TARGET_AUDIO_CODEC = "AAC-LC (aac, 2 channels)"
EXPIRE_DATE = date(2027, 10, 10)
PROCESS_ALL = "处理视频和音频"
PROCESS_VIDEO_ONLY = "仅处理视频"
PROCESS_AUDIO_ONLY = "仅处理音频"
PROCESS_MODES = [PROCESS_ALL, PROCESS_VIDEO_ONLY, PROCESS_AUDIO_ONLY]

VIDEO_CODEC_PROFILES = {
    "H.264": {
        "label": "H.264 / AVC（主流兼容，推荐）",
        "args": [
            "-c:v",
            "libx264",
            "-profile:v",
            "baseline",
            "-level",
            "4.0",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "medium",
            "-crf",
            "23",
        ],
    },
    "H.265": {
        "label": "H.265 / HEVC（体积更小，兼容性较低）",
        "args": ["-c:v", "libx265", "-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "28"],
    },
    "MPEG-4": {
        "label": "MPEG-4 Part 2（老设备兼容）",
        "args": ["-c:v", "mpeg4", "-q:v", "4", "-pix_fmt", "yuv420p"],
    },
    "Copy": {
        "label": "复制原视频编码（不重新编码）",
        "args": ["-c:v", "copy"],
    },
}

AUDIO_CODEC_PROFILES = {
    "AAC": {
        "label": "AAC-LC（主流兼容，推荐）",
        "args": ["-c:a", "aac", "-b:a", "128k", "-ac", "2"],
    },
    "MP3": {
        "label": "MP3（老设备兼容）",
        "args": ["-c:a", "libmp3lame", "-b:a", "160k", "-ac", "2"],
    },
    "Opus": {
        "label": "Opus（网络传输友好）",
        "args": ["-c:a", "libopus", "-b:a", "96k"],
    },
    "Copy": {
        "label": "复制原音频编码（不重新编码）",
        "args": ["-c:a", "copy"],
    },
}


def resolve_executable(path_or_name):
    """支持用户选择 exe 路径，也支持直接使用 PATH 中的 ffmpeg/ffprobe。"""
    value = path_or_name.strip()
    if not value:
        return ""
    if os.path.exists(value):
        return value
    return shutil.which(value) or ""


def bundled_executable(name):
    """优先从脚本目录、exe 旁边或 PyInstaller 解包目录查找 ffmpeg/ffprobe。"""
    candidate_dirs = [os.path.dirname(os.path.abspath(__file__))]
    if getattr(sys, "frozen", False):
        candidate_dirs.append(os.path.dirname(sys.executable))
        candidate_dirs.append(getattr(sys, "_MEIPASS", ""))

    for directory in candidate_dirs:
        if not directory:
            continue
        candidate = os.path.join(directory, name)
        if os.path.exists(candidate):
            return candidate
    return ""


def initial_ffmpeg_path():
    """开发期不写死本机路径；打包分发时可自动使用内置 ffmpeg.exe。"""
    return bundled_executable("ffmpeg.exe")


def packaged_ffmpeg_path():
    """打包版本强制优先使用内置 ffmpeg.exe。"""
    return bundled_executable("ffmpeg.exe")


def resolve_runtime_ffmpeg(selected_path):
    """运行时解析 FFmpeg：先用用户选择，其次回退内置文件。"""
    return resolve_executable(selected_path) or packaged_ffmpeg_path()


def is_expired(today=None):
    """到期日当天仍可使用，超过到期日后禁止打开。"""
    current_day = today or date.today()
    return current_day > EXPIRE_DATE


def show_expired_message():
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror("软件已过期", f"当前工具有效期至 {EXPIRE_DATE.isoformat()}，已过期,请联系分发人员重新分发。")
    root.destroy()


def infer_ffprobe_path(ffmpeg_path):
    """优先使用 ffmpeg.exe 同目录下的 ffprobe.exe，找不到再回退到 PATH。"""
    ffmpeg = resolve_executable(ffmpeg_path)
    if ffmpeg and os.path.dirname(ffmpeg):
        candidate = os.path.join(os.path.dirname(ffmpeg), "ffprobe.exe")
        if os.path.exists(candidate):
            return candidate
    bundled = bundled_executable("ffprobe.exe")
    if bundled:
        return bundled
    return resolve_executable("ffprobe")


def build_convert_command(
    ffmpeg_path,
    input_path,
    output_path,
    process_mode=PROCESS_ALL,
    video_profile_key="H.264",
    audio_profile_key="AAC",
):
    """生成面向内网旧播放器兼容性的 MP4 转码命令。"""
    command = [
        ffmpeg_path,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        input_path,
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-sn",
        "-dn",
    ]
    if process_mode in (PROCESS_ALL, PROCESS_VIDEO_ONLY):
        command.extend(VIDEO_CODEC_PROFILES[video_profile_key]["args"])
    else:
        command.append("-vn")

    if process_mode in (PROCESS_ALL, PROCESS_AUDIO_ONLY):
        command.extend(AUDIO_CODEC_PROFILES[audio_profile_key]["args"])
    else:
        command.append("-an")

    command.extend(["-movflags", "+faststart", output_path])
    return command


def probe_streams(ffprobe_path, video_path):
    command = [
        ffprobe_path,
        "-v",
        "error",
        "-show_entries",
        (
            "stream=index,codec_type,codec_name,profile,width,height,pix_fmt,"
            "sample_rate,channels,bit_rate"
        ),
        "-of",
        "json",
        video_path,
    ]
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "ffprobe 读取视频编码信息失败")
    return json.loads(result.stdout or "{}").get("streams", [])


def _first_stream(streams, codec_type):
    return next((item for item in streams if item.get("codec_type") == codec_type), {})


def format_stream_report(source_path, streams, target_video_codec, target_audio_codec):
    video = _first_stream(streams, "video")
    audio = _first_stream(streams, "audio")

    video_size = "未知"
    if video.get("width") and video.get("height"):
        video_size = f"{video.get('width')}x{video.get('height')}"

    audio_desc = audio.get("codec_name", "无音频流")
    if audio:
        audio_parts = [audio_desc]
        if audio.get("sample_rate"):
            audio_parts.append(f"{audio.get('sample_rate')}Hz")
        if audio.get("channels"):
            audio_parts.append(f"{audio.get('channels')}声道")
        audio_desc = " / ".join(audio_parts)

    lines = [
        f"源文件: {source_path}",
        f"源视频编码: {video.get('codec_name', '未知')}",
        f"源视频配置: {video.get('profile', '未知')}",
        f"源分辨率: {video_size}",
        f"源像素格式: {video.get('pix_fmt', '未知')}",
        f"源音频编码: {audio_desc}",
        f"目标视频编码: {target_video_codec}",
        f"目标音频编码: {target_audio_codec}",
    ]
    return "\n".join(lines)


class VideoConverter(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")

        self.title("视频兼容转码工具")
        self.geometry("1180x780")
        self.minsize(1080, 700)

        self.ffmpeg_path = tk.StringVar(
            value=initial_ffmpeg_path()
        )
        self.output_dir = tk.StringVar()
        self.worker_count = tk.StringVar(value="1")
        self.process_mode = tk.StringVar(value=PROCESS_ALL)
        self.video_profile_key = tk.StringVar(value="H.264")
        self.audio_profile_key = tk.StringVar(value="AAC")
        self.status_text = tk.StringVar(value="等待开始")
        self.success_count = tk.IntVar(value=0)
        self.fail_count = tk.IntVar(value=0)
        self.total_count = tk.IntVar(value=0)

        self._configure_tree_style()
        self._build_layout()

    def _configure_tree_style(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "Video.Treeview",
            background="#ffffff",
            fieldbackground="#ffffff",
            foreground="#111827",
            rowheight=46,
            borderwidth=0,
            font=("Microsoft YaHei UI", 13),
        )
        style.configure(
            "Video.Treeview.Heading",
            background="#e5e7eb",
            foreground="#111827",
            relief="flat",
            font=("Microsoft YaHei UI", 13, "bold"),
        )
        style.map(
            "Video.Treeview",
            background=[("selected", "#2563eb")],
            foreground=[("selected", "#ffffff")],
        )

    def _build_layout(self):
        self.configure(fg_color="#eef2f7")
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._build_header()

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=18, pady=(8, 18))
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(body, fg_color="#ffffff", corner_radius=14, border_width=1, border_color="#dbe3ef")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(1, weight=1)
        left.grid_rowconfigure(2, weight=1)

        right = ctk.CTkScrollableFrame(
            body,
            fg_color="#ffffff",
            corner_radius=14,
            border_width=1,
            border_color="#dbe3ef",
            scrollbar_button_color="#cbd5e1",
            scrollbar_button_hover_color="#94a3b8",
        )
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)

        self._build_task_panel(left)
        self._build_side_panel(right)
        self._build_log_panel(left)

    def _build_header(self):
        header = ctk.CTkFrame(self, fg_color="#ffffff", corner_radius=0)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        title = ctk.CTkLabel(
            header,
            text="视频兼容转码工具",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=24, weight="bold"),
            text_color="#0f172a",
        )
        title.grid(row=0, column=0, sticky="w", padx=20, pady=(16, 3))

        subtitle = ctk.CTkLabel(
            header,
            text="批量转换为 MP4 + H.264 baseline + yuv420p + AAC，解决内网播放器编码不支持问题",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=13),
            text_color="#64748b",
        )
        subtitle.grid(row=1, column=0, sticky="w", padx=20, pady=(0, 16))

    def _build_task_panel(self, parent):
        top = ctk.CTkFrame(parent, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 10))
        top.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            top,
            text="批量任务",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=20, weight="bold"),
            text_color="#0f172a",
        ).grid(row=0, column=0, sticky="w")

        actions = ctk.CTkFrame(top, fg_color="transparent")
        actions.grid(row=0, column=1, sticky="e")
        ctk.CTkButton(actions, text="添加视频", width=96, command=self.add_videos).grid(row=0, column=0, padx=(0, 8))
        ctk.CTkButton(actions, text="移除选中", width=96, fg_color="#64748b", hover_color="#475569", command=self.remove_selected).grid(
            row=0, column=1, padx=(0, 8)
        )
        ctk.CTkButton(actions, text="清空", width=72, fg_color="#dc2626", hover_color="#b91c1c", command=self.clear_videos).grid(
            row=0, column=2
        )

        table_frame = ctk.CTkFrame(parent, fg_color="#f8fafc", corner_radius=10, border_width=1, border_color="#dbe3ef")
        table_frame.grid(row=1, column=0, sticky="nsew", padx=14)
        table_frame.grid_columnconfigure(0, weight=1)
        table_frame.grid_rowconfigure(0, weight=1)

        self.video_table = ttk.Treeview(
            table_frame,
            columns=("index", "name", "status", "path"),
            show="headings",
            selectmode="extended",
            style="Video.Treeview",
        )
        self.video_table.heading("index", text="序号")
        self.video_table.heading("name", text="文件名")
        self.video_table.heading("status", text="状态")
        self.video_table.heading("path", text="完整路径")
        self.video_table.column("index", width=72, minwidth=60, anchor="center", stretch=False)
        self.video_table.column("name", width=360, minwidth=260, anchor="w")
        self.video_table.column("status", width=120, minwidth=100, anchor="center", stretch=False)
        self.video_table.column("path", width=620, minwidth=360, anchor="w")
        self.video_table.grid(row=0, column=0, sticky="nsew", padx=1, pady=1)

        scrollbar = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.video_table.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.video_table.configure(yscrollcommand=scrollbar.set)

    def _build_side_panel(self, parent):
        ctk.CTkLabel(
            parent,
            text="转码配置",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=18, weight="bold"),
            text_color="#0f172a",
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(16, 10))

        self.ffmpeg_entry, self.ffmpeg_browse_button = self._path_row(parent, 1, "FFmpeg", self.ffmpeg_path, self.select_ffmpeg)
        self._apply_packaged_ffmpeg_state()
        self._path_row(parent, 2, "输出目录", self.output_dir, self.select_output_dir)

        config = ctk.CTkFrame(parent, fg_color="#f8fafc", corner_radius=12, border_width=1, border_color="#e2e8f0")
        config.grid(row=3, column=0, sticky="ew", padx=16, pady=(8, 12))
        config.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(config, text="处理内容", text_color="#475569").grid(row=0, column=0, sticky="w", padx=12, pady=(12, 8))
        self._option_menu(config, values=PROCESS_MODES, variable=self.process_mode).grid(
            row=0, column=1, sticky="ew", padx=12, pady=(12, 8)
        )
        ctk.CTkLabel(config, text="并发线程", text_color="#475569").grid(row=1, column=0, sticky="w", padx=12, pady=8)
        self._option_menu(config, values=["1", "2", "3", "4"], variable=self.worker_count).grid(
            row=1, column=1, sticky="ew", padx=12, pady=8
        )
        ctk.CTkLabel(config, text="视频编码", text_color="#475569").grid(row=2, column=0, sticky="w", padx=12, pady=8)
        self._option_menu(
            config,
            values=list(VIDEO_CODEC_PROFILES.keys()),
            variable=self.video_profile_key,
        ).grid(
            row=2, column=1, sticky="ew", padx=12, pady=8
        )
        ctk.CTkLabel(config, text="音频编码", text_color="#475569").grid(row=3, column=0, sticky="w", padx=12, pady=(8, 12))
        self._option_menu(
            config,
            values=list(AUDIO_CODEC_PROFILES.keys()),
            variable=self.audio_profile_key,
        ).grid(
            row=3, column=1, sticky="ew", padx=12, pady=(8, 12)
        )

        stats = ctk.CTkFrame(parent, fg_color="#f8fafc", corner_radius=12, border_width=1, border_color="#e2e8f0")
        stats.grid(row=4, column=0, sticky="ew", padx=16, pady=(0, 12))
        for column in range(3):
            stats.grid_columnconfigure(column, weight=1)
        self._stat_box(stats, 0, "总数", self.total_count, "#38bdf8")
        self._stat_box(stats, 1, "成功", self.success_count, "#22c55e")
        self._stat_box(stats, 2, "失败", self.fail_count, "#ef4444")

        self.progress = ctk.CTkProgressBar(parent, height=14)
        self.progress.grid(row=5, column=0, sticky="ew", padx=16, pady=(0, 10))
        self.progress.set(0)

        ctk.CTkLabel(parent, textvariable=self.status_text, text_color="#64748b").grid(
            row=6, column=0, sticky="w", padx=16, pady=(0, 14)
        )

        self.convert_btn = ctk.CTkButton(
            parent,
            text="开始批量转码",
            height=44,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=15, weight="bold"),
            command=self.start_conversion,
        )
        self.convert_btn.grid(row=7, column=0, sticky="ew", padx=16, pady=(0, 12))

        ctk.CTkButton(
            parent,
            text="清空日志",
            height=36,
            fg_color="#64748b",
            hover_color="#475569",
            command=self.clear_log,
        ).grid(row=8, column=0, sticky="ew", padx=16, pady=(0, 16))

    def _path_row(self, parent, row, label, variable, command):
        frame = ctk.CTkFrame(parent, fg_color="#f8fafc", corner_radius=12, border_width=1, border_color="#e2e8f0")
        frame.grid(row=row, column=0, sticky="ew", padx=16, pady=(0, 10))
        frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(frame, text=label, text_color="#475569").grid(row=0, column=0, sticky="w", padx=12, pady=(10, 4))
        entry = ctk.CTkEntry(frame, textvariable=variable, height=34)
        entry.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 12))
        button = ctk.CTkButton(frame, text="浏览", width=70, height=30, command=command)
        button.grid(row=1, column=1, padx=(0, 12), pady=(0, 12))
        return entry, button

    def _apply_packaged_ffmpeg_state(self):
        if not packaged_ffmpeg_path():
            return
        if getattr(sys, "frozen", False):
            self.ffmpeg_path.set("已内置 ffmpeg.exe，无需配置")
            self.ffmpeg_entry.configure(state="disabled")
            self.ffmpeg_browse_button.configure(state="disabled")

    def _option_menu(self, parent, values, variable=None, command=None):
        return ctk.CTkOptionMenu(
            parent,
            values=values,
            variable=variable,
            command=command,
            height=36,
            corner_radius=8,
            fg_color="#ffffff",
            button_color="#f8fafc",
            button_hover_color="#e2e8f0",
            text_color="#0f172a",
            dropdown_fg_color="#ffffff",
            dropdown_hover_color="#dbeafe",
            dropdown_text_color="#0f172a",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=14),
            dropdown_font=ctk.CTkFont(family="Microsoft YaHei UI", size=14),
            anchor="w",
        )

    def _stat_box(self, parent, column, title, variable, color):
        frame = ctk.CTkFrame(parent, fg_color="#ffffff", corner_radius=10, border_width=1, border_color="#e2e8f0")
        frame.grid(row=0, column=column, sticky="ew", padx=8, pady=10)
        ctk.CTkLabel(frame, text=title, text_color="#64748b").grid(row=0, column=0, pady=(8, 0))
        ctk.CTkLabel(
            frame,
            textvariable=variable,
            text_color=color,
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=20, weight="bold"),
        ).grid(row=1, column=0, pady=(0, 8))

    def _build_log_panel(self, parent):
        panel = ctk.CTkFrame(parent, fg_color="#f8fafc", corner_radius=12, border_width=1, border_color="#dbe3ef")
        panel.grid(row=2, column=0, sticky="nsew", padx=14, pady=14)
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            panel,
            text="详细日志",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=20, weight="bold"),
            text_color="#0f172a",
        ).grid(row=0, column=0, sticky="w", padx=12, pady=(10, 6))

        self.log_text = ctk.CTkTextbox(
            panel,
            height=190,
            fg_color="#ffffff",
            text_color="#111827",
            font=ctk.CTkFont(family="Consolas", size=14),
            wrap="word",
        )
        self.log_text.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))

    def set_worker_count(self, value):
        self.worker_count.set(value)

    def select_ffmpeg(self):
        path = filedialog.askopenfilename(title="选择 ffmpeg.exe", filetypes=[("Executable", "*.exe")])
        if path:
            self.ffmpeg_path.set(path)
            self.log(f"[信息] 已选择 FFmpeg: {path}")

    def select_output_dir(self):
        directory = filedialog.askdirectory(title="选择输出目录")
        if directory:
            self.output_dir.set(directory)
            self.log(f"[信息] 输出目录: {directory}")

    def add_videos(self):
        files = filedialog.askopenfilenames(
            title="选择视频文件",
            filetypes=[
                ("视频文件", "*.mp4 *.avi *.mov *.mkv *.flv *.wmv *.m4v *.mpg *.mpeg *.3gp *.ts"),
                ("所有文件", "*.*"),
            ],
        )
        existing = set(self.get_video_paths())
        for path in files:
            if path not in existing:
                index = len(self.video_table.get_children()) + 1
                self.video_table.insert("", tk.END, values=(index, os.path.basename(path), "等待", path))
                self.log(f"[添加] {path}")
                existing.add(path)
        self.total_count.set(len(self.get_video_paths()))

    def remove_selected(self):
        for item_id in self.video_table.selection():
            values = self.video_table.item(item_id, "values")
            self.video_table.delete(item_id)
            if values:
                self.log(f"[移除] {values[3]}")
        self.refresh_video_indexes()
        self.total_count.set(len(self.get_video_paths()))

    def clear_videos(self):
        for item_id in self.video_table.get_children():
            self.video_table.delete(item_id)
        self.total_count.set(0)
        self.success_count.set(0)
        self.fail_count.set(0)
        self.log("[清空] 已清空视频列表")

    def get_video_paths(self):
        paths = []
        for item_id in self.video_table.get_children():
            values = self.video_table.item(item_id, "values")
            if values:
                paths.append(values[3])
        return paths

    def refresh_video_indexes(self):
        for index, item_id in enumerate(self.video_table.get_children(), 1):
            values = list(self.video_table.item(item_id, "values"))
            if values:
                values[0] = index
                self.video_table.item(item_id, values=values)

    def set_video_status(self, input_path, status):
        def update():
            for item_id in self.video_table.get_children():
                values = list(self.video_table.item(item_id, "values"))
                if values and values[3] == input_path:
                    values[2] = status
                    self.video_table.item(item_id, values=values)
                    break

        self.after(0, update)

    def log(self, message):
        self.after(0, self._append_log, message)

    def _append_log(self, message):
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)

    def clear_log(self):
        self.log_text.delete("1.0", tk.END)

    def show_error(self, title, message):
        self.after(0, lambda: messagebox.showerror(title, message))

    def show_info(self, title, message):
        self.after(0, lambda: messagebox.showinfo(title, message))

    def set_convert_button_state(self, state):
        self.after(0, lambda: self.convert_btn.configure(state=state))

    def set_progress(self, completed, total):
        percent = completed / total if total else 0
        self.after(0, lambda: self.progress.set(percent))
        self.after(0, lambda: self.status_text.set(f"进度 {completed}/{total}，{int(percent * 100)}%"))

    def validate_inputs(self):
        ffmpeg = resolve_runtime_ffmpeg(self.ffmpeg_path.get())
        if not ffmpeg:
            self.show_error("错误", "请先选择正确的 ffmpeg.exe；打包版本会自动使用内置 ffmpeg.exe")
            return None

        ffprobe = infer_ffprobe_path(ffmpeg)
        if not ffprobe:
            self.show_error("错误", "未找到 ffprobe.exe，请确认它与 ffmpeg.exe 在同一目录")
            return None

        output_dir = self.output_dir.get().strip()
        if not output_dir:
            self.show_error("错误", "请选择输出目录")
            return None

        videos = self.get_video_paths()
        if not videos:
            self.show_error("错误", "请至少添加一个视频文件")
            return None

        os.makedirs(output_dir, exist_ok=True)
        worker_count = max(1, min(4, int(self.worker_count.get() or 1)))
        process_mode = self.process_mode.get()
        video_profile_key = self.video_profile_key.get()
        audio_profile_key = self.audio_profile_key.get()
        return ffmpeg, ffprobe, output_dir, videos, worker_count, process_mode, video_profile_key, audio_profile_key

    def convert_one_video(
        self,
        ffmpeg,
        ffprobe,
        output_dir,
        input_path,
        index,
        total,
        process_mode,
        video_profile_key,
        audio_profile_key,
    ):
        base_name = os.path.basename(input_path)
        name, _ = os.path.splitext(base_name)
        output_path = os.path.join(output_dir, f"{name}_compatible.mp4")
        start_time = time.time()
        log_prefix = f"[{index}/{total} {base_name}]"
        self.set_video_status(input_path, "转码中")

        self.log("")
        self.log("-" * 72)
        self.log(f"{log_prefix} 处理文件: {input_path}")
        self.log(f"{log_prefix} 输出文件: {output_path}")

        try:
            source_streams = probe_streams(ffprobe, input_path)
            self.log(f"{log_prefix} [源编码信息]")
            self.log(
                format_stream_report(
                    input_path,
                    source_streams,
                    VIDEO_CODEC_PROFILES[video_profile_key]["label"],
                    AUDIO_CODEC_PROFILES[audio_profile_key]["label"],
                )
            )

            command = build_convert_command(
                ffmpeg,
                input_path,
                output_path,
                process_mode=process_mode,
                video_profile_key=video_profile_key,
                audio_profile_key=audio_profile_key,
            )
            self.log(f"{log_prefix} [转码命令]")
            self.log(subprocess.list2cmdline(command))

            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )

            for line in process.stdout:
                text = line.strip()
                if not text:
                    continue
                if "frame=" in text or "time=" in text or "bitrate=" in text or "error" in text.lower():
                    self.log(f"{log_prefix} [FFmpeg] {text}")

            process.wait()
            elapsed = time.time() - start_time

            if process.returncode != 0:
                self.log(f"{log_prefix} [失败] FFmpeg 返回码: {process.returncode}")
                self.set_video_status(input_path, "失败")
                return False

            target_streams = probe_streams(ffprobe, output_path)
            output_size = os.path.getsize(output_path) / (1024 * 1024)
            self.log(f"{log_prefix} [转换后编码信息]")
            self.log(
                format_stream_report(
                    output_path,
                    target_streams,
                    VIDEO_CODEC_PROFILES[video_profile_key]["label"],
                    AUDIO_CODEC_PROFILES[audio_profile_key]["label"],
                )
            )
            self.log(f"{log_prefix} [成功] 耗时: {elapsed:.1f} 秒，输出大小: {output_size:.2f} MB")
            self.set_video_status(input_path, "成功")
            return True
        except Exception as exc:
            self.log(f"{log_prefix} [异常] {exc}")
            self.set_video_status(input_path, "失败")
            return False

    def run_conversion(self):
        inputs = self.validate_inputs()
        if not inputs:
            return

        ffmpeg, ffprobe, output_dir, videos, worker_count, process_mode, video_profile_key, audio_profile_key = inputs
        self.set_convert_button_state("disabled")
        self.set_progress(0, len(videos))
        self.success_count.set(0)
        self.fail_count.set(0)
        self.total_count.set(len(videos))

        self.log("")
        self.log("=" * 72)
        self.log("[任务] 开始视频兼容转码")
        self.log(f"[工具] FFmpeg: {ffmpeg}")
        self.log(f"[工具] FFprobe: {ffprobe}")
        self.log(f"[输出] 目录: {output_dir}")
        self.log(f"[处理] 内容: {process_mode}")
        self.log(f"[目标] 视频: {VIDEO_CODEC_PROFILES[video_profile_key]['label']}")
        self.log(f"[目标] 音频: {AUDIO_CODEC_PROFILES[audio_profile_key]['label']}")
        self.log(f"[数量] 待处理: {len(videos)}")
        self.log(f"[并发] 线程数: {worker_count}")
        self.log("=" * 72)

        for input_path in videos:
            self.set_video_status(input_path, "等待")

        completed = 0
        success_count = 0
        fail_count = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_map = {
                executor.submit(
                    self.convert_one_video,
                    ffmpeg,
                    ffprobe,
                    output_dir,
                    input_path,
                    index,
                    len(videos),
                    process_mode,
                    video_profile_key,
                    audio_profile_key,
                ): input_path
                for index, input_path in enumerate(videos, 1)
            }

            for future in concurrent.futures.as_completed(future_map):
                if future.result():
                    success_count += 1
                else:
                    fail_count += 1
                completed += 1
                self.success_count.set(success_count)
                self.fail_count.set(fail_count)
                self.set_progress(completed, len(videos))

        self.log("")
        self.log("=" * 72)
        self.log(f"[完成] 成功: {success_count}，失败: {fail_count}")
        self.log("=" * 72)
        self.set_convert_button_state("normal")
        self.show_info("完成", f"转换完成\n成功: {success_count}\n失败: {fail_count}")

    def start_conversion(self):
        thread = threading.Thread(target=self.run_conversion, daemon=True)
        thread.start()


if __name__ == "__main__":
    if is_expired():
        show_expired_message()
        sys.exit(1)
    app = VideoConverter()
    app.mainloop()
