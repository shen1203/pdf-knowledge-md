# PDF 转 Markdown

一个简单的本地网页工具：

1. 上传一份 PDF。
2. 自动转换成 Markdown。
3. 自动检查是否可能遗漏正文、关键数字、编号和重要要求。
4. 在线预览并下载 `.md` 文件。

当前版本：0.6.0。

## GitHub 部署

源码已托管到 GitHub 仓库：<https://github.com/shen1203/pdf-knowledge-md>

仓库中已包含 GitHub Pages 部署 workflow，推送到 `main` 分支后会自动发布静态展示页。注意：GitHub Pages 仅支持静态页面；当前 FastAPI 后端仍需单独部署到支持 Python 的云服务上。

## 安装

需要 Python 3.10+。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[web,test]"
python -m pip install "paddlepaddle==3.3.1"
python -m pip install -e ".[ocr]"
```

## 启动

```powershell
pdf-to-md-web
```

浏览器打开 <http://127.0.0.1:8000>，先选择转换模式，再上传 PDF：

- **完整转换**：尽量保留全部可提取正文，只做 Markdown 结构化和必要清理。
- **重点摘要**：从原文中抽取标题、步骤、关键要求和带数字、日期、型号或
  编号的句子，省略非重点内容。

重点摘要采用规则化抽取，不使用生成模型改写或补充业务事实。

转换页面只保留：

- 转换状态。
- 与原 PDF 的文本保留相似度。
- 主要改动说明。
- 完整模式的逐页检查，或摘要模式的抽取/省略行数。
- Markdown 预览。
- Markdown 下载。

不要求填写文档 ID、分类、版本或解析器，也没有人工审核、评分和 ZIP
审计包。

## 自动完整性检查

转换完成后，程序会把 PDF 可提取原文与 Markdown 进行机械比对：

- PDF 页数和 Markdown 源页标记。
- 每一页单独计算正文、关键事实和关键要求覆盖情况，避免总体比例掩盖单页遗漏。
- 正文四字符片段覆盖率。
- 数字、日期、百分比、型号和编号。
- 包含“必须、禁止、不得、注意、条件、步骤”等词的关键句。
- 没有文字层的页面自动使用 OCR，并继续保留原 PDF 页码。
- OCR 失败或没有识别出文字的页面单独标明，不会误报通过。

自动检查用于发现明显遗漏，不会调用生成式模型总结或补写原文，也不能证明
语义绝对正确。OCR 页面会记录识别方式和平均文字识别置信度。

## 相似度与改动说明

结果页的“与原 PDF 文本相似度”使用 PDF 文字层和 OCR 提取全文的四字符片段
在结果中的覆盖率。它衡量转换或摘要阶段的文本保留程度，不是语义正确率；
扫描页使用 OCR 自身的提取结果作为比较基线，因此也不是对图片识别准确率的
独立证明。

完整转换会说明：

- 是否主动摘要或删减正文。
- 标题、列表和段落如何转换为 Markdown。
- 是否增加源页码注释。
- 转换器是否移除了重复页眉、页脚或页码。

重点摘要会说明：

- 原文行数、抽取重点行数和主动省略行数。
- 重点选择规则。
- 关键数字、日期、型号和编号的保留数量。
- 摘要按哪些 PDF 页码重新组织。

## 自动 OCR

`PDF_MD_ENGINE=auto` 时，程序会先分析每页文字密度：

- 有足够文字层的页面使用 pypdf，速度快且不引入识别误差。
- 只有文字不足的页面交给 PaddleOCR PP-StructureV3。
- OCR 结果重新放回原页位置，并写入 `source-page` 页码标记。
- 默认保留页面方向和文字行方向识别，关闭表格结构、公式、图表、印章、区域检测
  和页面变形模型，控制内网模型包体积。普通表格文字仍会被 OCR，但不承诺恢复
  精确行列；Windows CPU 默认关闭 MKLDNN，以避开当前版面模型的不兼容执行
  路径。如业务文档需要复杂结构能力，可使用自定义 PaddleX 配置。
- 结果页显示 OCR 页数、文本保留相似度和主要改动。
- 某一页 OCR 失败时任务不会伪装成完整成功，会明确列出失败页。

PaddleOCR 首次运行会下载模型。可在联网电脑执行：

```powershell
python .\scripts\warmup_ocr.py
```

也可以直接运行完整的离线包准备脚本：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\prepare-offline-ocr.ps1
```

脚本会在不纳入 Git 的 `offline-ocr/` 中保存 Windows CPU 安装轮子和已经预热的
PaddleX 模型缓存。将源码和该目录复制到公司电脑后执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-offline-ocr.ps1
```

如公司使用自定义 PaddleX 离线模型配置，可通过 `PDF_MD_OCR_CONFIG` 指向配置
文件。Docling 仍是独立的可选版面解析器：

```powershell
python -m pip install -e ".[docling]"
```

## 配置

| 环境变量 | 默认值 | 用途 |
|---|---:|---|
| `PDF_MD_STORAGE_ROOT` | `storage` | 上传文件、任务数据库和转换结果目录 |
| `PDF_MD_MAX_UPLOAD_MB` | `50` | 单文件大小上限 |
| `PDF_MD_ENGINE` | `auto` | 后台自动选择的转换引擎 |
| `PDF_MD_OCR_CONFIG` | 空 | 可选的 PaddleX 离线模型配置文件 |
| `PDF_MD_WORKERS` | `1` | 同时转换任务数 |
| `PDF_MD_HOST` | `127.0.0.1` | 监听地址 |
| `PDF_MD_PORT` | `8000` | 监听端口 |

## 测试

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

当前共 14 项测试，覆盖完整转换、抽取式重点摘要、相似度与改动报告、
逐页自动完整性检查、单页遗漏检测、上传校验、结果预览、Markdown 下载、
数据库字段，以及只对缺少文字层页面执行 OCR 的混合 PDF 流程。

网站已使用 Edge 真实验证：

- 将 PDF 作为浏览器 `DataTransfer` 文件拖入上传区。
- 非 PDF 拖入时显示错误且不写入文件输入框。
- 拖入 PDF 后可以直接提交并完成转换。
- 结果页可以返回上一级，且在没有历史记录时仍可回到首页。
- 完整转换和重点摘要都能生成结果；摘要会省略普通背景行并保留关键型号、
  时限和警告码。

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
