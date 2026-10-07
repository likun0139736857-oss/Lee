# 微信公众号账号级检索与阅览

## 能力边界

账号级检索必须回答三个不同问题：

1. 是否真实检查了指定公众号，而不是只搜主题词；
2. 是否能判断检索窗口内有没有更新；
3. 是否能打开文章并提取标题、公众号、发布时间、原文链接和正文。

不得把 Creator Buddy 爆款池、通用网页搜索或标题中出现账号名称，记作账号覆盖。

## 后端顺序

| 顺序 | 后端 | 适用场景 | 覆盖等级 |
| --- | --- | --- | --- |
| 1 | 逐账号 RSS/JSON Feed | 纯后台 cron 或已授权 Feed | `confirmed` |
| 2 | 已登录微信 + Computer Use | Lee 当场查询，或登录补送 heartbeat 检测到可见桌面 | 历史页核验后为 `confirmed`；只完成平台搜索为 `partial` |
| 3 | 搜狗微信文章搜索 + 作者精确匹配 | 无登录态时的公开兜底 | `partial` |
| 4 | Creator Buddy 爆款池 | 补充热门选题 | 不计账号覆盖 |

### RSS/JSON Feed

在环境变量 `LEE_WECHAT_FEEDS_JSON` 中填写一个本地 JSON 文件路径。文件只保存账号到 Feed URL 的映射，不保存 API Key：

```json
{
  "红餐网": ["https://example.test/feeds/hongcan.rss"],
  "咖门": ["https://example.test/feeds/kamen.json"]
}
```

Feed 可来自 Lee 自建或已授权的 RSS 服务。只有 Feed 请求成功，才能把账号标记为 `confirmed`；没有新文章也算完成覆盖。

### 已登录微信

只在交互式桌面任务使用。Codex 登录补送 heartbeat 触发时，只要桌面未锁定、微信已登录且窗口可唯一定位，也属于交互式桌面任务：

1. 读取 `computer-use` 的完整规范与确认政策；
2. 主窗口必须按可执行文件角色定位：只把工具真实返回、应用路径以 `\Tencent\Weixin\Weixin.exe` 结尾且标题为“微信”的唯一窗口视为微信主窗口。`WeChatAppEx.exe` 是网页/小程序渲染进程，不是主窗口；即使二者标题都叫“微信”，也不构成主窗口不唯一；
3. 在任何搜索操作前保存一次“搜索前窗口基线”，至少记录所有 `WeChatAppEx.exe` 窗口的工具返回 ID、应用路径、标题和可访问性正文摘要。不得把既有的无关页面（例如网盘、网页文章或小程序）当作搜一搜结果，也不得让它阻塞主窗口定位；
4. 点击微信主窗口左上角搜索框，输入完整账号名，选择“公众号”精确结果；
5. 微信通常会在 `WeChatAppEx.exe` 的独立“搜一搜”窗口打开结果。主窗口恢复不代表检索结束；必须重新枚举窗口，只读取相对于搜索前窗口基线“新出现或内容发生变化”的 `WeChatAppEx.exe` 窗口，并核对其正文包含本次完整账号名及“公众号/账号/文章”等平台搜索语义；
6. 在可访问性树或页面正文中核对“账号名 - 账号”、公众号类型、认证/简介、账号更新时间和作者为该账号的结果。不要只靠头像或模糊文本判断身份；
7. 再进入账号主页或“文章”页，打开“全部/文章”历史页，记录窗口内文章标题、发布时间和原文；
8. 打开文章并读取正文；
9. 不发送消息、不关注/取关、不点赞、不评论；
10. 把本次核验结果写成下列本地 JSON；只有身份和历史页都已核对，才允许 `history_page_checked` 为 `true`；
11. 若账号身份和登录态搜索结果已核对，但历史页未打开，写 `platform_search_checked: true`、`history_page_checked: false`。统一雷达会保留这些当前线索并记为 `partial`，不会再用搜狗旧索引覆盖；
12. 只有按第 2 步仍找不到唯一的 `Weixin.exe` 主窗口、桌面锁定、登录失效、出现验证，或搜索后没有可核验的新/变化子窗口时，才停止该路径并记录具体失败层；不得笼统写“微信窗口不可定位”。

纯后台 cron 不得依赖该前台路径；附着于当前 Codex 任务、用于首次登录补送的 heartbeat 在前台条件满足时必须优先执行该路径。

```json
{
  "run_date": "2026-07-17",
  "accounts": [
    {
      "canonical_name": "红餐网",
      "checked_at": "2026-07-17T17:00:00+08:00",
      "account_identity_verified": true,
      "platform_search_checked": true,
      "history_page_checked": true,
      "articles": [
        {
          "title": "文章标题",
          "account_name": "红餐网",
          "published_at": "2026-07-16T19:30:00+08:00",
          "source_url": "",
          "summary": "可选摘要",
          "body_text": "打开文章后提取的正文，可为空"
        }
      ]
    }
  ]
}
```

日期栏显示“昨天、星期三”等相对时间时，必须结合 `run_date` 换算为绝对日期。然后传入统一雷达入口：

```text
powershell -File scripts/run_daily_radar.ps1 \
  -OutputDirectory outputs/lee-daily \
  -RunDate YYYY-MM-DD \
  -VerifiedWeChatInput outputs/wechat-desktop-verified.json
```

脚本会校验 `run_date`，并且只有 `account_identity_verified` 与 `history_page_checked` 同时为真时，将该账号记为 `confirmed`。
若 `account_identity_verified` 与 `platform_search_checked` 为真而 `history_page_checked` 为假，文章以 `wechat_authenticated_search` 后端进入雷达，覆盖记为 `partial`，同时保留 `current_hits`。

### 搜狗微信公开兜底

`scripts/wechat_account_retrieval.py` 使用文章搜索并对返回作者做别名精确匹配。搜狗索引可能延迟、缺失或返回旧文，因此：

- 请求成功只能标记 `partial`；
- 输出 `latest_indexed_at` 与 `index_freshness`；最新精确作者结果早于近三日窗口时必须记为 `stale`；
- `stale` 或没有可解析日期时写 `requires_wechat_verification: true`，不得把“请求成功”包装成完成了当前账号检查；
- 精确作者结果可作为选题线索；
- 零结果不代表公众号没有更新；
- 不解决验证码，不绕过频控；
- 搜狗跳转和微信临时链接可能过期，必须保留标题、作者、发布时间和搜索查询以便重建。

## 命令

后端体检：

```text
python scripts/wechat_account_retrieval.py doctor \
  --config config/editorial-source-pool.json --probe
```

抓取全部重点账号：

```text
python scripts/wechat_account_retrieval.py fetch \
  --config config/editorial-source-pool.json \
  --output outputs/lee-daily/YYYY-MM-DD/wechat-account-radar.json \
  --run-date YYYY-MM-DD --hot-days 3 --case-days 30 \
  --read-limit-per-account 1
```

只查一个账号：

```text
python scripts/wechat_account_retrieval.py fetch \
  --config config/editorial-source-pool.json \
  --output outputs/wechat-account-radar.json \
  --run-date YYYY-MM-DD --account 红餐网
```

读取已知微信文章：

```text
python scripts/wechat_account_retrieval.py read \
  --url "https://mp.weixin.qq.com/s/..."
```

## 覆盖台账

日报必须分别报告：

- `confirmed`：完成 Feed 或已登录微信账号页核验；
- `partial`：只完成公开索引作者精确查询；
- `unavailable`：所有账号级后端失败；
- `matched`：检索窗口内找到至少一篇作者精确匹配文章；
- `current_matched`：近三日窗口内找到作者精确匹配文章。

`partial` 和 `unavailable` 都属于“未确认完整覆盖”，不得写成“该账号没有更新”。
