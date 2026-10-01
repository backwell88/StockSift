# Changelog

## v1.4.0+portable.1 (2026-09-30)

- 版本号：v1.4.0+portable.1；应用业务版本仍为 v1.4.0。
- 变更摘要：建立不含 EXE、虚拟环境、构建缓存和用户数据的独立源码移植包。
- 修改文件：`README.md`、`Start_Windows.cmd`、`Test_Windows.cmd`、`Build_Windows.cmd`、`tools/build_windows.py`，并收录核心源码、测试及构建工具。
- 回滚方式：删除外层 `move` 文件夹；原项目文件没有被修改或删除。

最新版本 v1.4.0（2026-09-24）：预装默认策略和初始历史报告。完整变更及回退说明见文末 v1.4.0 条目。

## v1.3.1

- 版本号：v1.3.1。
- 变更摘要：腾讯模式股票名单改为腾讯 getBoardRankList，解除东方财富股票名单依赖；利润数据仍使用既有东财财务接口，不改变条件。
- 完整性：每页偏移、数量、总数、重复检查，取完复核总数；出现排名漂移时停止，不返回残缺股票池。
- 修改文件：stockreport/{providers,ui,__init__}.py、tools/{universe_probe,static_release}.py、tests/{test_core,test_live_adapters,test_v131}.py、README.md、相关交接/状态/开发文档及 REVIEW_v1.3.1.md、启动脚本、MANIFEST.sha256。
- 验证范围：有限网络探测、名单核对、静态审查及语法解析；未运行测试套件、GUI、全市场筛选、回测或AI请求。实际观察见 REVIEW_v1.3.1.md。
- 回滚：运行保留的 dist/v1.3.0/StockReport/StockReport.exe，或解压 dist/StockReport-v1.3.0-Windows-x64.zip；用户数据不变，旧版本恢复使用东财名单。

## v1.3.0

- 版本号：v1.3.0。
- 变更摘要：首页明确策略/条件组/条件，新增新建、精确删除当前载入版本、重命名；聊天删除独立差异框，增加Enter发送及Shift+Enter换行。
- 修改文件：stockreport/{ui,storage,__init__}.py、tests/test_v130.py、tools/static_release.py、README.md、docs/{REVIEW_v1.3.0,TEST_STATUS,LOCAL_CODEX_HANDOFF,DEVELOPER_GUIDE,WINDOWS_README}.md、MANIFEST.sha256、外层启动脚本及说明。
- 删除只针对已载入ID，其他版本和报告保留；数据库保留删除恢复记录，新版本编号不复用。最后一条策略禁止删除。
- 重命名另存新版本，不覆盖历史；AI建议仍须手动确认。
- 验证：静态审查与语法解析；不运行测试、GUI或联网取数。
- 回滚：运行保留的 dist/v1.2.1/StockReport/StockReport.exe 或解压 dist/StockReport-v1.2.1-Windows-x64.zip。已删除版本不会随程序回退自动恢复，恢复记录保存在本机数据库 deleted_versions 表。

## v1.2.1 (2026-09-23)

- 版本号：v1.2.1，静态审查修复版。
- 变更摘要：修复回测读取阻塞、来源初始化、日期跨度、取消绑定、不可见异常；完善报告排序、附件确认、CSV精度和文件失败清理。
- 修改文件：stockreport/{__init__,ui,backtest,providers,rules,storage,ai,exports}.py、tools/{build_windows,windows_acceptance,static_release}.py、tests/{test_ui,test_v120,test_v121}.py、README.md、docs/{REVIEW_v1.2.1,TEST_STATUS,LOCAL_CODEX_HANDOFF,DEVELOPER_GUIDE,WINDOWS_README}.md、MANIFEST.sha256，以及外层启动入口及说明。
- 验证范围：只做静态审查与语法检查及打包，不运行测试、GUI、行情或AI请求。旧版验收不代表本版。
- 回滚方式：运行保留的 dist/StockReport/StockReport.exe，或在另一个目录解压 dist/StockReport-v1.2.0-Windows-x64.zip；不删除用户数据。


## v1.2.0

- 版本号：v1.2.0。需求规格见 `docs/REQUIREMENTS_v1.2.0.md`。
- 变更摘要：修复“API Key 关闭后必须重填”；AI 聊天可读取用户勾选的本机报告；每次筛选新增可筛选排序的 CSV；新增历史报告一键回测。
- 修改文件：新增 `stockreport/exports.py`、`stockreport/backtest.py`、`tests/test_v120.py`、`docs/REQUIREMENTS_v1.2.0.md`；修改 `stockreport/ai.py`、`stockreport/storage.py`、`stockreport/providers.py`、`stockreport/ui.py`、`stockreport/__init__.py`、`launch.py`、`tests/test_ui.py`、`README.md`、`docs/WINDOWS_README.md`、`docs/DEVELOPER_GUIDE.md`、`docs/TEST_STATUS.md`、`docs/LOCAL_CODEX_HANDOFF.md`、`CHANGELOG.md`。
- 密钥存储：`settings` 表新增 `deepseek_api_key` 与 `keep_api_key`。勾选保存时写入本机 SQLite；不勾选维持旧的仅内存行为。密钥为**明文可逆存储**，README、设置页与开发者规范都明确说明，并提供“清除已保存密钥”。密钥仍不写入日志、报告、CSV、JSON 或交付包。
- AI 报告上下文：聊天页新增报告勾选列表，最多 3 份，单份 20000 字符、合计 60000 字符上限，超出会截断并提示。报告文本以“只读资料”区块注入系统提示，并声明报告文字不是指令；`strategy` 校验未放宽，越出 `reports` 目录的路径被拒绝。
- CSV：`exports.write_csv` 统一写出 UTF-8 BOM + CRLF 的 CSV，数值列不写千分位、缺失写空、以 `= + - @` 开头的文本做公式注入防护。筛选报告 CSV 含全部已检查股票与状态原因；运行页“导出当前表格为 CSV”导出的是当前筛选与排序后的结果。
- 回测：`backtest.py` 优先读取同名 `.json`（精确），缺失时解析 Markdown 并标注“近似值”。基准价与最新价取自同一次连续请求 `LiveProvider.bars_from`，避免前复权重算造成虚假涨跌。基准日停牌时取之前最近交易日并记录实际日期。演示报告、无入选股票、缺基准日期均明确拒绝。取不到数据标为“无法回测”，不填 0。回测文件命名独立，不覆盖旧文件，并写同名 JSON 审计。
- 数据约束：默认策略与阈值不变；不自动切换日线来源；不把未明确标注复权的数据当作前复权；请求间隔、403/429/501 停止策略不变。
- 界面：报告页改为“列表 + 可排序筛选表格 + Markdown 概览”，新增“回测”标签页，标签数由 5 增至 6；取消按钮文案随当前任务变化。
- 测试：79 项自动化测试全部通过（原 47 项保留）。
- 回滚方式：把 `stockreport/` 与 `launch.py` 换回 v1.1.0 版本，并删除 `stockreport/exports.py`、`stockreport/backtest.py`。用户数据目录无需迁移：v1.1.0 会忽略多出来的 `deepseek_api_key` 设置键，已生成的 CSV 与回测文件保持不动；v1.1.0 不会保存密钥，也不会读报告给 AI。

## v1.1.0 (2026-09-11)

- 版本号：v1.1.0。
- 变更摘要：股票名单改用实测可达的东财延迟节点，新增独立选择的腾讯前复权日线；保留旧源并说明其可用状态。连接检测展示全量分页、日期和具体连接错误，失败退出码修正。新安装默认腾讯，已有设置保留。
- 修改文件：`stockreport/providers.py`、`stockreport/engine.py`、`stockreport/ui.py`、`stockreport/__init__.py`、`launch.py`、`tests/test_live_adapters.py`、`tests/test_ui.py`、`tools/windows_acceptance.py`、`tools/build_windows.py`、`README.md`、`docs/DEVELOPER_GUIDE.md`、`docs/TEST_STATUS.md`、`docs/LOCAL_CODEX_HANDOFF.md`、`docs/test-output.txt`、`MANIFEST.sha256`；外层目录新增 `Start_StockReport.cmd`，更新入口说明。
- 数据约束：默认策略和阈值不变；不自动切换日线来源，不以未明确标注复权的数据代替前复权；JSON 增加实际来源及名单分页元数据。
- 批量稳定性：腾讯使用财经网页转发入口，同源前复权；请求间隔至少 0.8 秒。403/429/501 触发停止提交新股票并保存未完成报告，避免连续重试放大上游访问保护。
- 界面：增加明确切换腾讯并检测的按钮；连接结果注明本次来源，最小窗口也可完整显示三个结果。
- 回滚方式：在另一目录解压保留的 `dist/StockReport-v1.0.1-Windows-x64.zip` 并运行其中 EXE；默认用户数据目录沿用原路径，策略和报告格式不变。v1.0.1 不支持腾讯，若该版本读到 tencent 设置，手动切回 eastmoney 或 sina；旧版本的连接问题会恢复。

## v1.0.1 (2026-09-11)

- 版本号：v1.0.1
- 变更摘要：修复 Windows 下 SQLite 文件锁定和 UTF-8 测试问题；将净利润适配器改为实际提供合并累计 `NETPROFIT` 的东方财富完整利润表接口。
- 修改文件：`stockreport/storage.py`、`stockreport/ui.py`、`stockreport/providers.py`、`stockreport/__init__.py`、`tests/test_core.py`、`README.md`、`docs/DEVELOPER_GUIDE.md`、`docs/TEST_STATUS.md`、`CHANGELOG.md`
- 回滚方式：将上述文件替换为源码交付包 v1.0.0 中的同名文件；这会同时恢复旧的 SQLite 连接行为和当前已确认字段不匹配的 `RPT_DMSK_FN_INCOME` 财务适配器。
## v1.4.0 (2026-09-24)

- 新增首次使用资料：Jayson缩量回调策略默认载入、内置2026-09-24真实历史报告；已有数据不覆盖，不带入密钥。
- 修改：stockreport/bootstrap.py、storage.py、__init__.py、starter/，tools/prepare_starter.py、build_windows.py、static_release.py，tests/test_starter.py，以及README/验收/交付说明和外层启动入口。
- 验证范围：静态审查、语法及资源哈希核对、构建；不运行测试套件/GUI/行情扫描。
- 回退：运行保留的 dist/v1.3.1/StockReport/StockReport.exe。

