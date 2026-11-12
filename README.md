# Jiaocai-Downloader

国家中小学智慧教育平台电子教材 PDF 下载器，由 [MRMZSoft](https://github.com/MRMZSoft) 维护。

[项目主页](https://github.com/MRMZSoft/Jiaocai-Downloader) · [问题反馈](https://github.com/MRMZSoft/Jiaocai-Downloader/issues) · [国家中小学智慧教育平台](https://basic.smartedu.cn/tchMaterial)

## 界面预览

![Jiaocai-Downloader 当前界面](demo.png)

## 功能

- **教材 PDF 下载**：解析普通电子教材、专题教材及课程资源包中提供的 PDF。
- **自动命名**：以资源标题作为默认文件名，处理文件名中的非法字符；专题中的同名文件自动加编号。
- **进度与取消**：显示整体下载进度，支持停止当前任务，保留已完成文件。
- **下载检查**：检查 HTTP 状态、PDF 文件头，并在条件允许时核对文件大小和 MD5。

可用于平台提供的**小学、小学（五·四学制）、初中、初中（五·四学制）、高中及特殊教育**教材。

## 从源码运行

需要 **Python 3.10+**、Tkinter 图形界面环境和网络连接。

进入项目目录，安装依赖并运行：

```powershell
python -m pip install -r requirements.txt
python Jiaocai-Downloader.py
```

也可以使用独立的虚拟环境，避免与其他项目的依赖混用：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe Jiaocai-Downloader.py
```

第三方依赖为 `requests` 和 `ttkbootstrap`，版本范围见 [requirements.txt](requirements.txt)。若提示缺少 `tkinter`，请检查 Python 安装时是否包含 Tcl/Tk 组件。


## 使用方法

1. 打开[平台教材页面](https://basic.smartedu.cn/tchMaterial)，或点击程序菜单中的“教材页面”。
2. 选择所需教材，复制其**资源详情页网址**。
3. 将网址粘贴到程序的 **URL** 输入框，每次输入一个页面链接。
4. 点击 **START**，等待程序解析资源。
5. 选择保存位置，等待下载结果提示。

网址格式示例，请使用实际教材页面的地址：

```text
https://basic.smartedu.cn/tchMaterial/detail?contentType=assets_document&contentId=资源ID
```

### 保存文件

弹出文件保存窗口，默认文件名为资源标题，可以自行修改文件名和保存位置。同名目标文件由保存对话框确认是否覆盖。

程序先在目标文件夹写入临时文件，通过下载检查后再保存为最终文件。下载失败或取消时会清理未完成的临时文件。

### 停止下载

点击 **STOP** 取消当前任务。程序会在当前请求返回或收到下一块数据时处理取消操作，因此停止可能需要等待；已完成的文件保留。

暂不支持断点续传。


## 第三方来源与使用说明

资源详情接口路由、文件选择规则和匿名请求头参考了 [tchMaterial-parser v4.4](https://github.com/happycola233/tchMaterial-parser/tree/v4.4)。上游版权声明及 MIT 许可文本见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)，也可查看[上游许可证](https://github.com/happycola233/tchMaterial-parser/blob/v4.4/LICENSE)。

- 本项目为非官方工具，与国家中小学智慧教育平台及教材出版方没有隶属或合作关系。
- 程序从平台资源地址下载文件到用户指定的位置，不托管或分发教材内容。教材版权及相关权利归各自权利人所有，软件许可证不包含教材内容的授权。
- 请仅下载、保存和使用你有权使用的资源，并遵守平台条款及适用法律。能够取得文件不代表已经获得下载、复制或再分发授权。
- 本项目仅供个人学习、研究，未经作者许可，不得将本项目源码及构建产物用于商业用途；第三方代码的使用条件以其各自许可证为准。
