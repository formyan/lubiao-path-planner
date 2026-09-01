# 腾讯云 EdgeOne Pages 部署指引（方案 A：国内可访问的全功能展示站）

本方案把「前端静态站」和「AI 生成路径」一起部署到腾讯云 EdgeOne Pages：

- 前端：`index.html` / `guide.html` / `styles.css` / `js/`（纯静态，国内访问流畅）
- 后端：`functions/api/health.js` 与 `functions/api/ai-plan.js`（EdgeOne Pages Functions，自动映射为 `/api/health` 与 `/api/ai-plan`）
- AI：调用 DeepSeek API，密钥只保存在 EdgeOne 服务端环境变量中，**不会出现在浏览器或前端代码里**

部署完成后，访问 `https://你的项目名.edgeone.app` 即可体验全部功能，不需要自己买域名。

---

## 一、准备账号

1. 打开[腾讯云官网](https://cloud.tencent.com/)，注册并完成**实名认证**（个人认证即可，免费）。
2. 在控制台搜索「EdgeOne」，进入 **EdgeOne 边缘安全加速平台**。
3. 若提示开通，按页面提示开通 EdgeOne 服务（Pages 功能按用量免费额度使用，超出才计费）。

> 说明：EdgeOne Pages 的免费额度一般覆盖个人演示站用量（每月有函数调用次数与构建次数额度）。AI 调用费来自你自己的 DeepSeek API，与 EdgeOne 计费无关。

## 二、准备 DeepSeek API 密钥

1. 登录 [DeepSeek 开放平台](https://platform.deepseek.com/)。
2. 在「API Keys」中创建密钥，形如 `sk-xxxxxxxx`。
3. 确认账户有余额（DeepSeek 为预付费，按 token 计费）。
4. **复制密钥备用，只粘贴到 EdgeOne 控制台，不要发到聊天、代码仓库或任何公开地方。**

## 三、上传站点到 EdgeOne Pages

### 方式 1：直接上传（推荐，最快）

1. 在 EdgeOne 控制台左侧菜单进入 **Pages** → **创建项目**。
2. 项目名称填写小写字母/数字/连字符，例如 `path-planner-demo`。
3. 部署方式选择 **直接上传**（有的界面叫「上传静态资源」）。
4. 选择本项目的站点根目录：`outputs/path-planner`，并把以下内容拖入上传框：
   - `index.html`
   - `guide.html`
   - `styles.css`
   - `js/`（整个文件夹）
   - `functions/`（整个文件夹，**必须包含，否则 AI 接口不可用**）
5. 点击 **部署**，等待构建/发布完成。

> 注意：不要上传 `.env`、`server.py`、`start-server.bat`、`.git` 等本地运行文件；直接上传模式下只挑上面的文件即可。

### 方式 2：从 Git 导入（自动更新）

1. 先把代码推送到 GitHub（本项目已是 Git 仓库）。
2. 在 EdgeOne Pages 创建项目时选择 **Git 导入**，授权 GitHub，选择 `path-planner` 仓库。
3. 构建配置：
   - 构建命令：留空（纯静态项目无需构建）
   - 输出目录：留空或填 `/`（仓库根目录即站点根目录）
4. 每次 `git push` 到 main 分支后，EdgeOne 会自动重新部署。

## 四、配置环境变量（密钥与防护）

项目创建/部署完成后，进入项目的 **设置 → 环境变量**（或「变量与密钥」），添加以下变量：

| 变量名 | 是否必填 | 示例 | 说明 |
|---|---|---|---|
| `DEEPSEEK_API_KEY` | 必填 | `sk-xxxxxxxx` | DeepSeek API 密钥，仅存服务端 |
| `AI_MODEL` | 选填 | `deepseek-v4-flash` | 使用的模型名，默认即此值 |
| `RATE_LIMIT` | 选填 | `10` | 每 IP 每小时最大请求数，防刷，默认 10 |
| `DAILY_QUOTA` | 选填 | `200` | 全站每日最大 AI 请求数，默认 200 |
| `DEMO_ACCESS_CODE` | 选填 | 留空 | 演示访问码；设置后前端必须带 `X-Demo-Code` 请求头才可用 |

保存后，重新部署一次（或等待环境变量生效）。

> **关于演示访问码**：当前前端页面还没有输入访问码的界面。若你设置了 `DEMO_ACCESS_CODE`，访问者将无法直接在页面上输入，因此**建议暂不启用**，仅靠 `RATE_LIMIT` 与 `DAILY_QUOTA` 防刷。后续若需要公开展示且不希望被滥用，可以再给我说，我帮你加上访问码输入界面。

## 五、验证部署

部署完成后：

1. 打开 `https://你的项目名.edgeone.app`，应看到站点首页。
2. 访问 `https://你的项目名.edgeone.app/api/health`，应返回 JSON：

```json
{
  "ok": true,
  "ai": true,
  "model": "deepseek-v4-flash",
  "envFile": true,
  "needCode": false
}
```

其中 `"ai": true` 表示密钥配置成功。

3. 回到首页，点击「AI 生成路径」，输入当前信息与目标后应能正常生成。

## 六、常见问题

**页面显示「API 未配置」？**
说明 `/api/health` 返回的 `ai` 为 false，或接口无法访问。请检查：
- 环境变量 `DEEPSEEK_API_KEY` 是否已保存并重新部署；
- 上传时是否包含了 `functions/` 文件夹；
- 直接打开 `/api/health` 看返回内容。

**提示密钥无效 / 401？**
检查密钥是否以 `sk-` 开头、有没有多余空格、账户是否有余额。

**AI 请求被拒（429 / 限流提示）？**
演示站有每 IP 每小时与全站每日配额，可在环境变量中调大 `RATE_LIMIT` / `DAILY_QUOTA`。

**想要正式域名？**
在 EdgeOne 控制台可以绑定已备案域名；也可以先在「DNS/域名管理」里把备案过的域名 CNAME 到 Pages 域名。不备案的域名无法在国内正常访问，所以演示阶段直接用 `edgeone.app` 默认域名即可。

## 七、成本与安全小结

- EdgeOne Pages：免费额度内使用，个人演示通常足够。
- DeepSeek API：按实际生成 token 计费，由你账户承担；页面限流可控制成本。
- 密钥安全：只存在 EdgeOne 服务端环境变量，前端与仓库均不含密钥（`.env` 已被 `.gitignore` 排除）。
