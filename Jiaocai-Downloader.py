from __future__ import annotations

import queue
import sys
import threading
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tkinter import Menu, filedialog, messagebox, ttk

from ttkbootstrap import Progressbar, Style

from jiaocai_core import (
    DownloadCancelled, ResourceError, SmartEduClient,
    safe_filename,
)


def prepare_tasks(resources, directory):
    """专题里的多个 PDF 自动命名，保留已有文件。"""
    tasks, reserved = [], set()
    for resource in resources:
        name = safe_filename(resource.title, "pdf")
        destination = directory / name
        suffix = 2
        while str(destination).casefold() in reserved or destination.exists():
            destination = directory / f"{Path(name).stem} ({suffix}).pdf"
            suffix += 1
        reserved.add(str(destination).casefold())
        tasks.append((str(len(tasks)), resource, destination))
    return tasks


class DownloaderApp:
    def __init__(self):
        # 保持原版窗口、字体、主题、控件尺寸及排列。
        self.style = Style(theme="cosmo")
        self.style.configure(".", font=("System", 20))
        self.style.configure("TButton", font=("System", 20), padding=4)
        self.root = self.style.master
        self.root.title("国家中小学智慧教育平台电子教材下载器")
        self.root.geometry("450x200")
        self.root.resizable(False, False)
        icon_dir = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
        if (icon_dir / "icon.ico").exists():
            self.root.iconbitmap(str(icon_dir / "icon.ico"))

        self.events = queue.Queue()
        self.cancel_event = threading.Event()
        self.busy = False
        self.closing = False
        self.states = {}
        self.warnings = []
        self.worker = None
        self.build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(80, self.process_events)

    def build_ui(self):
        self.url_label = ttk.Label(self.root, text="URL")
        self.url_label.pack()
        self.url_entry = ttk.Entry(self.root, width=50)
        self.url_entry.pack()
        self.progress_bar = Progressbar(self.root, orient="horizontal", length=400,
                                       mode="determinate", maximum=100, bootstyle="success")
        # 不同 ttkbootstrap 版本的默认高度不同，保持原来的 18 像素占位。
        self.progress_bar.pack(pady=10, ipady=max(0, (18 - self.progress_bar.winfo_reqheight()) // 2))
        self.button_frame = ttk.Frame(self.root)
        self.button_frame.pack(pady=5)
        self.download_button = ttk.Button(self.button_frame, text="START", command=self.start_download)
        self.download_button.pack(side="left", padx=5)
        self.stop_button = ttk.Button(self.button_frame, text="STOP", command=self.cancel_download, state="disabled")
        self.stop_button.pack(side="left", padx=5)

        self.menubar = Menu(self.root)
        self.root.config(menu=self.menubar)
        self.menubar.add_command(label="工作原理", command=lambda: messagebox.showinfo(
            "工作原理", "从教材页面读取资源 ID，查询平台资源详情并获取 PDF 地址。\n"
            "使用平台的匿名下载接口。", parent=self.root))
        self.menubar.add_command(label="关于我们", command=lambda: messagebox.showinfo(
            "关于我们", "Copyright © 2023-2026 MRMZSoft All Rights Reserved.\n"
            "访问我们的 GitHub 主页：https://github.com/MRMZSoft\n\n"
            "项目地址：https://github.com/MRMZSoft/Jiaocai-Downloader\n"
            "如果你觉得还不错，欢迎 Star！\n\n"
            "资源解析参考 tchMaterial-parser v4.4。\n"
            "第三方声明（上游 MIT 许可证）：\n"
            "https://github.com/happycola233/tchMaterial-parser/blob/v4.4/LICENSE", parent=self.root))
        self.menubar.add_command(label="教材页面", command=lambda: webbrowser.open("https://basic.smartedu.cn/tchMaterial"))

    def set_busy(self, busy):
        self.busy = busy
        self.download_button.config(state="disabled" if busy else "normal")
        self.stop_button.config(state="normal" if busy else "disabled")

    def start_download(self):
        if self.busy:
            return
        url = self.url_entry.get().strip()
        if not url:
            messagebox.showerror("错误", "请先填写教材页面的网址。", parent=self.root)
            return
        self.states.clear()
        self.warnings.clear()
        self.cancel_event = threading.Event()
        self.progress_bar["value"] = 0
        self.set_busy(True)
        self.worker = threading.Thread(target=self.resolve_page,
                                       args=(url, self.cancel_event), daemon=True)
        self.worker.start()

    def resolve_page(self, url, cancel):
        try:
            with SmartEduClient(cancel=cancel) as client:
                resolution = client.resolve(url)
                client.check_cancelled()
            self.events.put(("resolved", resolution, cancel))
        except DownloadCancelled:
            self.events.put(("finished", "下载已取消。"))
        except ResourceError as error:
            self.events.put(("finished", str(error)))
        except Exception:
            self.events.put(("finished", "资源解析失败，请重试。"))

    def choose_destination(self, resolution, cancel):
        if cancel.is_set():
            self.finish("下载已取消。")
            return
        resources = resolution.resources
        self.warnings = resolution.warnings
        if len(resources) == 1:
            save_path = filedialog.asksaveasfilename(parent=self.root, defaultextension=".pdf",
                filetypes=[("PDF Files", "*.pdf")], initialfile=safe_filename(resources[0].title, "pdf"))
            tasks = [("0", resources[0], Path(save_path))] if save_path else []
        else:
            directory = filedialog.askdirectory(title=f"该资源包含 {len(resources)} 个 PDF，请选择保存文件夹", parent=self.root)
            tasks = prepare_tasks(resources, Path(directory)) if directory else []
        if not tasks:
            self.set_busy(False)
            if self.closing:
                self.root.destroy()
            return
        if cancel.is_set():
            self.finish("下载已取消。")
            return
        self.states = {task_id: {"title": resource.title, "status": "pending", "received": 0,
                                "total": resource.size, "detail": str(destination)}
                       for task_id, resource, destination in tasks}
        self.worker = threading.Thread(target=self.run_downloads, args=(tasks, cancel), daemon=True)
        self.worker.start()

    def run_downloads(self, tasks, cancel):
        try:
            with ThreadPoolExecutor(max_workers=3) as executor:
                futures = [executor.submit(self.run_download, *task, cancel) for task in tasks]
                for future in futures:
                    future.result()
            self.events.put(("finished", None))
        except Exception:
            self.events.put(("finished", "任务异常结束，请重试；已完成的文件会保留。"))

    def run_download(self, task_id, resource, destination, cancel):
        try:
            with SmartEduClient(cancel=cancel) as client:
                client.download(resource, destination,
                                lambda received, total: self.events.put(("progress", task_id, received, total)))
            self.events.put(("state", task_id, "completed", str(destination)))
        except DownloadCancelled:
            self.events.put(("state", task_id, "cancelled", "下载已取消，未完成的临时文件已清理。"))
        except ResourceError as error:
            self.events.put(("state", task_id, "failed", str(error)))
        except Exception:
            self.events.put(("state", task_id, "failed", "下载发生异常，请重试。"))

    def process_events(self):
        # 工作线程只发送事件，所有对话框和控件操作均在主线程。
        for _ in range(300):
            try:
                kind, *args = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "resolved":
                self.choose_destination(*args)
            elif kind == "progress":
                task_id, received, total = args
                self.states[task_id].update(received=received, total=total)
                all_total = sum(state["total"] for state in self.states.values())
                all_received = sum(state["received"] for state in self.states.values())
                self.progress_bar["value"] = min(100, all_received * 100 / all_total) if all_total else 0
            elif kind == "state":
                task_id, status, detail = args
                self.states[task_id].update(status=status, detail=detail)
            elif kind == "finished":
                self.finish(args[0])
            if self.closing and not self.busy:
                return
        self.root.after(80, self.process_events)

    def finish(self, error=None):
        self.set_busy(False)
        if self.closing:
            self.root.destroy()
            return
        if error:
            messagebox.showerror("下载失败", error, parent=self.root)
            return
        completed = [state for state in self.states.values() if state["status"] == "completed"]
        failed = [state for state in self.states.values() if state["status"] in ("failed", "cancelled")]
        if failed or self.warnings:
            details = [f"{state['title']}：{state['detail']}" for state in failed] + self.warnings
            message = f"成功 {len(completed)} 个 PDF。\n" + "\n".join(details[:6])
            if len(details) > 6:
                message += f"\n另有 {len(details) - 6} 项未完成。"
            messagebox.showwarning("下载结果", message, parent=self.root)
        elif completed:
            self.progress_bar["value"] = 100
            messagebox.showinfo("下载成功", "文件已保存" if len(completed) == 1 else f"{len(completed)} 个 PDF 文件已保存", parent=self.root)

    def cancel_download(self):
        self.cancel_event.set()
        self.stop_button.config(state="disabled")

    def on_close(self):
        if self.busy:
            if messagebox.askyesno("退出", "下载尚未结束，是否停止下载并退出？", parent=self.root):
                self.closing = True
                self.cancel_download()
        else:
            self.root.destroy()


def main():
    app = DownloaderApp()
    app.root.mainloop()


if __name__ == "__main__":
    main()
