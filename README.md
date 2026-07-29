# PDF 转 Markdown

一个简单的本地网页工具：

1. 上传一份 PDF。
2. 自动转换成 Markdown。
3. 自动检查是否可能遗漏正文、关键数字、编号和重要要求。
4. 在线预览并下载 `.md` 文件。

当前版本：0.3.0。

## 安装

需要 Python 3.10+。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[web,test]"
```

## 启动

```powershell
pdf-to-md-web
```

浏览器打开 <http://127.0.0.1:8000>，选择 PDF 后点击“上传并转换”。

转换页面只保留：

- 转换状态。
- 自动完整性检查结论。
- Markdown 预览。
- Markdown 下载。

不要求填写文档 ID、分类、版本或解析器，也没有人工审核、评分和 ZIP
审计包。

## 自动完整性检查

转换完成后，程序会把 PDF 可提取原文与 Markdown 进行机械比对：

- PDF 页数和 Markdown 源页标记。
- 正文四字符片段覆盖率。
- 数字、日期、百分比、型号和编号。
- 包含“必须、禁止、不得、注意、条件、步骤”等词的关键句。

自动检查用于发现明显遗漏，不会调用生成式模型总结或补写原文，也不能证明
语义绝对正确。扫描型 PDF 如果没有可提取文本，程序会明确提示无法验证；
这类文件需要安装 OCR 引擎。

## 默认转换能力

默认安装使用 pypdf，适合可以复制文字的 PDF。Docling 和 PaddleOCR 适配器
仍保留在项目中，后续处理复杂排版或扫描件时可以安装：

```powershell
python -m pip install -e ".[docling]"
python -m pip install -e ".[ocr]"
```

PaddleOCR 安装前需要根据服务器 CPU/GPU 环境选择对应 PaddlePaddle 版本。

## 配置

| 环境变量 | 默认值 | 用途 |
|---|---:|---|
| `PDF_MD_STORAGE_ROOT` | `storage` | 上传文件、任务数据库和转换结果目录 |
| `PDF_MD_MAX_UPLOAD_MB` | `50` | 单文件大小上限 |
| `PDF_MD_ENGINE` | `auto` | 后台自动选择的转换引擎 |
| `PDF_MD_WORKERS` | `1` | 同时转换任务数 |
| `PDF_MD_HOST` | `127.0.0.1` | 监听地址 |
| `PDF_MD_PORT` | `8000` | 监听端口 |

## 测试

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

当前共 8 项测试，覆盖 PDF 转换、自动完整性检查、上传校验、结果预览、
Markdown 下载、版本处理和扫描件风险。

## 数据与迁移

运行数据保存在 `storage/`，不会提交到 Git。项目目录可以复制到另一台电脑，
重新创建虚拟环境并安装依赖后运行。

GitHub 仓库只保存源码和测试，不保存：

- 业务 PDF。
- 转换后的 Markdown。
- `storage/` 数据库和上传文件。
- `.env`、Token、证书和密码。
- Python 虚拟环境与模型文件。

当前网站尚未接入公司统一认证，不应直接暴露到公网。正式部署到公司内网时，
再补充认证、反向代理和服务器运行配置。

## 可选 CLI

网站是主要入口，项目仍保留批量处理 CLI：

```powershell
pdf-to-md engines
pdf-to-md inspect .\input\manual.pdf
pdf-to-md convert .\input\manual.pdf --output .\knowledge
pdf-to-md batch .\input --recursive --output .\knowledge
```
