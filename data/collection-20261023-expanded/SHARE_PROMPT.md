# 可直接发给其他对话的提示词

## 同一项目/同一台电脑的新对话

复制下面这段：

```text
你在处理开封旅游项目。资料目录是：
/Users/mac/Desktop/大挑/kaifeng-tourism-mvp/data/collection-20261023-expanded

先只读，不要修改或导入运行库。请按顺序读取：
1. HANDOFF.md
2. reports/expanded-report-20261023.md
3. structured/source-manifest-expanded.json
4. 我问题涉及的结构化文件；需要核对原文时再读 raw/

这是 2026-10-23 的候选包，不是正式验收数据：
- target_business_date=null，采集日不能当游玩日
- 全部 unverified、internal_only、is_demo=false
- formal_acceptance=false，没有可执行路线
- 不得把平台推荐菜当门店供应证明
- 不得把地图中心点当入口
- 不得把历史/第三方时刻外推为目标日规则
- 不得补造场次结束、公交班次、排队、菜单或现场走查

回答时请引用具体文件名和 JSON 字段；证据不足就直接标“待核实”，不要猜。
```

## 不同项目、不同电脑或云端对话

1. 上传 `kaifeng-tourism-expanded-20261023.zip`。
2. 解压后确认顶层目录为 `collection-20261023-expanded/`。
3. 复制下面这段，并把 `<解压目录>` 换成实际绝对路径：

```text
我上传的是开封旅游扩展资料候选包。请先解压并找到：
<解压目录>/collection-20261023-expanded/HANDOFF.md

先读 HANDOFF.md、reports/expanded-report-20261023.md 和 structured/source-manifest-expanded.json，再按问题读取结构化文件。需要原文时读 raw/。

使用约束：
- 这是 2026-10-23 的候选包，不是已验证运营事实
- 不得把采集日当游玩日
- 不得把平台推荐菜当供应证明
- 不得把地图中心点当入口
- 不得把历史或第三方时刻外推为目标日规则
- 不得补造演出结束、公交班次、排队、菜单或现场走查
- 全部 unverified、internal_only，正式验收前不得写入运行库或生成可执行路线

请先告诉我你实际读取了哪些文件、当前数据缺口是什么，再开始回答具体问题。
```

## 只讨论某个子问题时的简短提示

```text
读取开封扩展候选包：
/Users/mac/Desktop/大挑/kaifeng-tourism-mvp/data/collection-20261023-expanded

先读 HANDOFF.md。只使用 unverified 候选，不要升级为 verified；涉及目标日营业、演出结束、餐馆供应、入口通行或交通班次时必须标“待核实”。回答附文件名和字段依据。
```

