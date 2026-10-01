# Stock Report Desktop 源码移植包

这是 Stock Report Desktop v1.4.0 的项目目录。`function/` 存放程序、测试和工具代码；运行数据保存在项目内的 `data/`。

## 新电脑快速开始

### 1. 安装 Python

在 Windows 上安装 64 位 Python 3.10 或更高版本，建议 Python 3.12 或 3.13。安装时保留 Tcl/Tk，并勾选将 Python 加入 PATH。

验证安装：

```powershell
python --version
python -c "import tkinter; print(tkinter.TkVersion)"
```

### 2. 直接运行源码

双击：

```text
Start_Windows.cmd
```

本程序运行时只使用 Python 标准库，不需要先安装第三方业务依赖。

也可以在终端运行：

```powershell
python launch.py
```

### 3. 运行测试

双击 `Test_Windows.cmd`，或者运行：

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'function')
python -m unittest discover -s function\tests -v
```

### 4. 构建 Windows EXE（可选）

双击 `Build_Windows.cmd`。脚本会：

1. 在本地 `.venv` 不存在时创建它；已有时复用；
2. 安装 `requirements-build.txt` 中的 PyInstaller；
3. 运行测试；
4. 在 `dist\v1.4.0\StockReport` 生成带图标的 Windows 成品。之后版本号变化时，构建会写入新的 `dist\v版本号\StockReport` 目录。

第一次安装 PyInstaller 时需要联网。发布时必须复制对应版本的整个 `StockReport` 文件夹，不能只复制 EXE。构建与测试产生的临时文件放在项目内的 `build/`。

请使用 `Build_Windows.cmd` 构建；若直接运行 `StockReport.spec`，需自行指定同一版本的 `--distpath dist/v1.4.0`，否则 PyInstaller 会使用默认的无版本 `dist/` 路径。

图标文件在 `function/stockreport/assets/`；需要调整图案时，可运行 `function/tools/make_icon.ps1` 重新生成，再构建 EXE。

## 目录结构

```text
StockSift/
├── launch.py                  程序入口：GUI、演示、接口检测、回测
├── function/
│   ├── stockreport/           核心程序
│   │   ├── ui.py              Tkinter 桌面界面
│   │   ├── rules.py           策略、指标、校验与计算
│   │   ├── providers.py       腾讯、东方财富、新浪数据适配
│   │   ├── engine.py          并发筛选与结果分类
│   │   ├── storage.py         SQLite、策略版本与报告保存
│   │   ├── exports.py         Markdown/CSV/JSON 输出
│   │   ├── backtest.py        历史报告价格回测
│   │   ├── ai.py              DeepSeek 策略辅助
│   │   ├── bootstrap.py       首次启动资料导入
│   │   ├── assets/            EXE 与窗口共用的图标
│   │   └── starter/           首次启动所需的预置资料
│   ├── tests/                 自动化测试
│   └── tools/                 构建、发布与验收工具
├── data/                      本机策略、报告、设置和可能保存的 API Key
├── dist/v1.4.0/StockReport/   当前版本的 Windows 成品文件夹
├── requirements-build.txt     仅构建阶段使用的第三方依赖
├── StockReport.spec           PyInstaller 配置
├── Start_Windows.cmd          源码启动脚本
├── Test_Windows.cmd           测试脚本
└── Build_Windows.cmd          建立环境并打包
```

## 程序数据

默认数据目录是项目根目录下的：

```text
data\
```

其中保存 SQLite 配置、策略版本和历史报告。复制整个 `StockSift` 目录时也会复制 `data/`，其中可能包含个人策略、报告和已保存的 DeepSeek API Key；分享项目时请先检查是否包含这些数据。

DeepSeek API Key 可以在设置页填写。选择保存时，密钥以明文写入本机 SQLite，并非加密保险库，请只在可信的个人电脑上使用。

## 数据与回测口径

- 默认行情来源为腾讯前复权日线；东方财富和新浪作为明确可选的数据来源。
- 净利润来自东方财富财务接口。
- 程序不会在数据源失败时偷偷切换到另一来源。
- 数据不足、接口失败和策略不符合是不同状态，不会使用零值代替缺失数据。
- “回测”用于重新获取历史报告中股票的连续价格序列并比较价格，不是完整的策略历史重放或收益预测。
- 外部行情接口可能变化，接口检测成功只代表检测当时可用。

## 主要命令

```powershell
python launch.py
python launch.py --demo --data-dir .\demo-data
python launch.py --probe tencent
python launch.py --backtest "报告路径.json" --source tencent --data-dir .\demo-data
$env:PYTHONPATH = (Join-Path (Get-Location) 'function')
python -m unittest discover -s function\tests -v
```

## Python 版本

原始 v1.4.0 成品使用 Python 3.12.4 构建。源码设计目标为 Python 3.10 及以上；迁移到其他 Python 小版本后，应先运行完整测试，再重新打包。

