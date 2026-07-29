# PDF Knowledge MD

面向企业 AI 客服/RAG 知识库的可审计 PDF 转 Markdown 管线。

当前版本是 Web MVP 0.2，已经支持：

- 浏览器上传 PDF、查看转换任务和质量结果。
- Markdown 在线预览、单文件下载和完整审计 ZIP 下载。
- SQLite 任务记录和进程内后台任务执行。
- 显式业务文档 ID，文件迁移到另一台电脑后仍可沿用同一知识标识。
- `/health/live` 和 `/health/ready` 健康检查接口。
- 单文件和目录批量转换。
- PDF 文本密度分析，区分原生文本、混合型和扫描件。
- `auto`、`pypdf`、`docling`、`paddleocr` 解析器路由。
- 无重型模型时使用 pypdf 完成文本型 PDF 基线转换。
- 重复页眉/页脚清理和源页码注释。
- SHA-256 去重、不可变版本目录和原子 `current.json` 指针。
- `source.pdf`、`document.md`、`manifest.json` 完整审计产物。
- 页面完整性、乱码和扫描件风险等基础质量门禁。
- 低质量候选版本默认不更新当前线上指针。

## 1. 环境

- Python 3.10+
- 默认核心依赖：pypdf
- 可选解析器：Docling、PaddleOCR PP-StructureV3

开发模式安装：

```powershell
python -m pip install -e .
```

安装网站及测试依赖：

```powershell
python -m pip install -e ".[web,test]"
```

安装 Docling：

```powershell
python -m pip install -e ".[docling]"
```

安装 PaddleOCR 前，应先根据服务器 CPU/GPU 环境选择官方支持的
PaddlePaddle 安装方式，然后安装：

```powershell
python -m pip install -e ".[ocr]"
```

首次使用 Docling 或 PaddleOCR 时可能需要下载模型。生产环境应提前缓存、
锁定模型版本，并完成代码、模型和权重许可证审查。

也可以不安装项目，直接在仓库根目录设置 `PYTHONPATH=src` 后运行：

```powershell
$env:PYTHONPATH = "src"
python -m pdf_to_md engines
```

## 2. 启动网站

从仓库根目录执行：

```powershell
pdf-to-md-web
```

浏览器打开 <http://127.0.0.1:8000>。首次启动会自动创建本机
`storage/` 目录和 SQLite 数据库。上传页面当前支持单份 PDF，填写业务文档
ID 后即可转换；成功后可查看质量报告、预览 Markdown，并下载 `.md` 或
包含源 PDF、Markdown 和 manifest 的 ZIP。

也可以直接使用模块启动：

```powershell
python -m uvicorn pdf_to_md.web.app:app --host 127.0.0.1 --port 8000
```

支持以下环境变量：

| 变量 | 默认值 | 用途 |
|---|---:|---|
| `PDF_MD_STORAGE_ROOT` | `storage` | 上传文件、SQLite、知识版本和导出的持久化根目录 |
| `PDF_MD_MAX_UPLOAD_MB` | `50` | 单个上传文件大小上限（MB） |
| `PDF_MD_ENGINE` | `auto` | 默认转换引擎 |
| `PDF_MD_WORKERS` | `1` | 进程内转换线程数 |
| `PDF_MD_PROCESS_INLINE` | `false` | 测试时可设为 `true`，生产不建议 |

当前版本尚未接入登录和文档权限校验，只应绑定 `127.0.0.1` 做开发验证，
不得直接暴露到公网或全公司网络。内网正式部署需要先完成统一认证、反向代理、
受限 worker 和持久化备份。

## 3. CLI 使用

查看可用引擎：

```powershell
pdf-to-md engines
```

转换前检查 PDF：

```powershell
pdf-to-md inspect .\input\manual.pdf
```

转换单个 PDF：

```powershell
pdf-to-md convert .\input\manual.pdf --document-id CS-MANUAL-001 --output .\knowledge
```

`--document-id` 是稳定的业务标识，迁移目录或服务器时不应改变。若省略，
程序会使用文件路径生成兼容旧版的标识，不适合作为跨机器长期主键。

递归转换目录：

```powershell
pdf-to-md batch .\input --recursive --output .\knowledge
```

指定解析器：

```powershell
pdf-to-md convert .\input\manual.pdf --engine docling
pdf-to-md convert .\input\scan.pdf --engine paddleocr
```

`auto` 路由规则：

1. 混合型或扫描型 PDF 且 PaddleOCR 已安装：使用 PaddleOCR。
2. 否则 Docling 已安装：使用 Docling。
3. 否则使用 pypdf 基线引擎。

pypdf 遇到疑似扫描件时会生成候选版本，但质量状态为
`review_required`，默认不会更新 `current.json`。仅在明确接受风险时使用：

```powershell
pdf-to-md convert .\input\scan.pdf --allow-review-required
```

不建议在生产知识库中使用该参数绕过人工复核。

## 4. 输出

```text
knowledge/
  <document_id>/
    current.json
    versions/
      <version_id>/
        source.pdf
        document.md
        manifest.json
```

- `versions/` 中的版本不可变，便于回滚和审计。
- `current.json` 是当前已发布版本的权威指针，通过同目录临时文件和
  `os.replace` 原子更新。
- 同一路径 PDF 的 SHA-256 未变化时，重复执行会返回 `skipped`。
- `--force` 可以保留同一源文件的新转换版本，适用于引擎或规则升级后的重跑。

## 5. 退出码

- `0`：转换已发布，或文件未变化而跳过。
- `1`：输入、依赖或转换错误。
- `2`：转换已完成，但质量门禁要求复核或判定失败。

批量命令会继续处理其他 PDF，并在 JSON 输出中列出每份文档的结果。

## 6. 测试

仓库测试使用 Python 标准库 `unittest`。带有 `reportlab` 时会生成临时
PDF，验证转换、页眉页脚清理、版本发布、去重和扫描件拦截：

```powershell
$env:PYTHONPATH = "src"
python -m pip install -e ".[web,test]"
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

当前自动化测试共 7 项，覆盖转换发布、重复跳过、扫描件拦截、稳定业务
文档 ID、Web 上传、状态查询、Markdown 预览和文件下载。

## 7. 可迁移交付

项目源码和配置使用相对路径，业务数据目录已被 `.gitignore` 排除，可以将
整个项目目录压缩后传到公司电脑，再推送至公司组织的私有 GitHub 仓库。
公司电脑首次验证流程：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[web,test]"
python -m unittest discover -s tests -v
pdf-to-md-web
```

GitHub 只托管源码、测试和后续部署配置，不应提交 `storage/`、业务 PDF、
转换产物、模型权重、密码、Token、证书或 `.env`。Docker 和 GitHub
Actions 自动部署按当前决定延后，待功能与公司真实样本验证稳定后实现。

## 8. 当前边界

- pypdf 是文本型 PDF 的基线，不负责 OCR 和复杂表格恢复。
- Docling 与 PaddleOCR 适配器已接入，但需要在真实公司样本上安装、压测和
  校验，当前环境尚未完成模型级验证。
- 网站 MVP 当前为单文件上传；任务由同一 Web 进程的线程池执行，不是生产级
  Redis/独立 worker 队列。
- 网站尚未实现统一登录、ACL、人工批准、版本回滚、批量上传和知识库推送。
- PaddleOCR 生成的图片资源还未写入 `assets/`。
- 尚未实现 RAG 分块和 Embedding/BM25 索引。
- 不能仅凭命令成功就认为内容准确；生产发布必须结合 `manifest.json`
  的质量结果和样本人工复核。
