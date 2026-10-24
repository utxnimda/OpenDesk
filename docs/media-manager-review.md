# 作品管理器 0.3.0 代码审查

日期：2026-10-25。范围聚焦于 OpenDesk 接入边界、工作区与资源路径、配置/资料持久化、进程关闭和构建边界。已有业务界面继续由插件自身维护，没有将整套业务强行改成宿主 Vue 组件；本记录不等同于对每个历史业务算法逐行审计。

## 已修复

| 级别 | 问题及影响 | 修复 |
|---|---|---|
| P1 | 配置隐式扫描当前目录和父目录，可能因启动位置不同读到别的资料库 | 资源根/工作区根必须为绝对路径；默认配置只取已选工作区及包内模板；显式缺失或冲突报错 |
| P1 | 收集记录未知版本、坏结构可被默认值替代，并在保存时覆盖 | 读取与锁内保存前共用版本/结构校验；未知格式拒绝，原字节不变；旧列表格式明确兼容 |
| P2 | framework/browse/bootstrap 配置错误被忽略 | 拒绝损坏格式和未知显式版本；只对已定义的缺省和旧格式保持兼容 |
| P2 | 插件关闭先应答成功再清理，失败后控制通道无法重试 | 关闭完成才应答；失败保留通道、服务和互斥锁，可再次关闭 |
| P2 | 初次保存设置或原生窗口导航失败，已有服务被误认为已就绪 | 区分监听服务与初始化完成；重试补完未完成阶段，正常调用不重载页面 |
| P2 | 模块设置与偏好没有版本边界 | 新写扁平 schemaVersion:1，读取旧裸对象不改字节，未知版本/非法字段拒绝；API 剥离元数据 |
| P2 | 通用宿主构建器硬编码某个业务的 --nimda 参数 | 改用 --project；插件保留旧参数兼容并拒绝冲突；构建别名仍经过公开清单校验 |

相关实现与回归：

- [路径与环境变量](../plugins/media-manager/apps/framework/backend/work_catalog_yaml/layout.py)、[桌面 bootstrap](../plugins/media-manager/apps/framework/backend/work_catalog_yaml/desktop.py)、[浏览配置](../plugins/media-manager/apps/framework/backend/work_catalog_yaml/jp_tv/browse_settings.py)。
- [收集记录持久化](../plugins/media-manager/apps/features/collection-info/backend/collection_info/service.py)及 test_jp_tv_collection_records.py。
- [模块控制器](../plugins/media-manager/integrations/opendesk/nimda_opendesk/controller.py)、[控制通道](../plugins/media-manager/integrations/opendesk/nimda_opendesk/worker.py)及 test_adapter.py。
- [统一分发入口](../scripts/distribution.mjs)、[模块构建器](../scripts/module-pack.mjs)及 distribution.test.mjs。

## 命名与数据边界

可见名称、当前源码/资料目录、开发命令与独立 EXE 统一为作品管理器 / Media Manager / media-manager。稳定安装 ID、已有 RPC、偏好键及锁名保留为兼容接口，避免设置丢失与新旧实例并写。旧历史记录不改写成新版本结果。

业务资料独立由 SVN 管理，模块连接/展示偏好由 runtime 管理；新配置版本只作用于其明确拥有的文件。规范环境变量和显式路径错误不触发猜测另一个资料库。

## 模式建议与验证边界

建议保留现有普通模块和完整应用两类接入；展示方式由模块按需选择。作品管理器的嵌入与弹窗共用一套服务，真正独立 EXE 是另一启动入口。现有宿主尚不支持任意本地 EXE 管理，不能把“弹出窗口”称作脱离宿主运行。详见[模式评估](application-modes.md)。

已执行的完整测试、构建与实际读取结果统一记录在[验证记录](verification.md)。源码回归、冻结程序运行和原生界面点击验证分别记录，不能互相替代。
