# 归置檔设计指南 · Editorial Precision

> 一套可复用的浅色高端前端设计语言。
> 起源于「归置檔 · Dedup」，适用于工具型、数据型、效率型 Web 产品。
> 核心公式：**瑞士的骨架 × Craft 的细节 × 日式的呼吸**

---

## 一、设计哲学

| 原则 | 含义 | 反面 |
|------|------|------|
| 排版即设计 | 用字体、字重、字距、留白分层，而不是用装饰 | 渐变、发光、厚重阴影 |
| 单一信号色 | 全站只有一个主题色，语义色低饱和、小面积 | 彩虹式多色系统 |
| 纸张的质感 | 暖白底色 + 近黑墨色，像一本精装手册 | 纯白死灰的"默认网页感" |
| 动效服务节奏 | 快速反馈（≤200ms）+ 延迟呈现（hover 停留） | 为炫技而动画 |
| 数字是主角 | 关键数据用衬线大字号，成为视觉焦点 | 数字和正文一视同仁 |

**一句话气质**：像一本瑞士设计的精装产品手册——克制、精确、有纸张的温度。

---

## 二、色彩系统

### 2.1 中性色（纸与墨）

| Token | 色值 | 用途 |
|-------|------|------|
| `--bg` | `#faf9f6` | 页面底色（暖白/和纸感） |
| `--bg-deep` | `#f4f2ec` | 比底色更深一级的纸色（特殊容器如 AI 输入框） |
| `--surface` | `#ffffff` | 卡片、Header、弹窗 |
| `--border` | `#e7e4dc` | 常规分隔细线 |
| `--border-strong` | `#d6d2c7` | 强调边框、输入框边 |
| `--text` | `#1c1c1e` | 正文墨色（近黑，不用纯黑） |
| `--text-dim` | `#8a867e` | 次级文字（**暖调灰**，不用冷灰） |

### 2.2 主题色（三选一，可切换）

| 主题 | 色值 | 气质 | 出处 |
|------|------|------|------|
| 朱砂 | `#c8432b` | 印章红，东方感、有温度 | 默认 |
| 玄青 | `#2e4756` | 深海青灰蓝，沉稳专业 | 与朱砂是经典"青赤配" |
| 墨绿 | `#3f6b4f` | 松烟墨绿，安静内敛 | 与朱砂拉开冷暖 |

选色要点：三色同属**矿物颜料**家族（低饱和、带灰度），摆在一起气质统一。避免"办公紫""糖果蓝"这类数码感强的颜色。

### 2.3 语义色（压低饱和度）

| Token | 色值 | 备注 |
|-------|------|------|
| `--success` | `#6a8f6b` | 苔绿 |
| `--warning` | `#b98a2f` | 琥珀 |
| `--danger` | `#b5402f` | 固定红色，不随主题色变化 |
| `*-soft` | 主色 + 8~10% 透明度 | 用于标签底色、focus 光晕 |

### 2.4 主题切换架构（CSS 变量 + data 属性）

```css
:root, :root[data-theme="cinnabar"] {
  --accent: #c8432b; --accent-deep: #a83824; --accent-soft: rgba(200,67,43,.08);
}
:root[data-theme="indigo"] {   /* 玄青 */
  --accent: #2e4756; --accent-deep: #243945; --accent-soft: rgba(46,71,86,.09);
}
:root[data-theme="pine"] {     /* 墨绿 */
  --accent: #3f6b4f; --accent-deep: #33573f; --accent-soft: rgba(63,107,79,.09);
}
```

- 全部组件只引用 `var(--accent)`，切换时零改动
- 切换器 = 三枚 14px 圆点，选中态用双层 box-shadow 描边
- 选择持久化到 `localStorage`，页面加载时立即应用（脚本放在内容脚本之前，避免闪烁）

---

## 三、字体系统（本语言的灵魂）

三套字体各司其职，**不可混用**：

| 角色 | 字体栈 | 用在哪 |
|------|--------|--------|
| 衬线 `--font-serif` | Source Serif 4 / Noto Serif SC / Songti SC | 大数字、产品名、卡片标题、弹窗标题 |
| 无衬线 `--font` | Inter / PingFang SC / Microsoft YaHei | 正文、按钮、标签 |
| 等宽 `--font-mono` | JetBrains Mono / SF Mono / Consolas | 路径、大小、时间、编号、badge、日志 |

**关键手法**：

- **衬线大数字**：统计数字 34px serif bold，一眼高级。公式：`font-family: var(--font-serif); font-variant-numeric: tabular-nums;`
- **等宽路径**：文件路径/日期/大小用 11.5px mono，多行数据自然对齐如列车时刻表
- **编号系统**：卡片用 CSS counter 生成 `01 02 03`（`decimal-leading-zero`），mono 字体 + 右侧细分隔线
- **字重克制**：只用 400 / 500 / 600 / 700 四档；正文 400，强调最多 600
- **字距呼吸**：中文标题 `letter-spacing: 1~3px`；小标签 `letter-spacing: 1~2px`；产品名可到 3px

---

## 四、形状与质感

| 项 | 值 | 说明 |
|----|----|------|
| 圆角 | 卡片 8px / 控件 5px / 标签 2~3px | 小圆角，瑞士式利落 |
| 阴影 | `0 1px 2px rgba(28,28,30,.04), 0 4px 16px rgba(28,28,30,.05)` | 极轻弥散，几乎不可见 |
| 大阴影 | `0 2px 6px ... .06, 0 16px 48px ... .12` | 仅弹窗/浮层 |
| 分层手段 | **1px 细线 + 留白**，不用阴影堆叠 | 细线色 `#e7e4dc` |
| 渐变 | ❌ 禁止 | 包括按钮、进度条、文字 |
| 发光 | ❌ 禁止 | 无 glow、无 neon |
| 缓动 | `cubic-bezier(.22,.9,.32,1)` | 统一 `--ease` |

---

## 五、组件模式

### 5.1 统计带（不用卡片！）
四个统计项放进**一整条白底容器**，项与项之间用 `border-left` 细竖线分隔；衬线大数字在上，小号中文标签在下（`letter-spacing: 3px`）；最重要的指标（如"可节省"）用主题色，其余保持墨色。

### 5.2 编号卡片（数据组）
- 白底 + 1px 细边 + 8px 圆角
- 标题区左侧：mono 编号 `01` + 竖细线 + serif 文件名
- 右上角关键数值（如节省空间）：serif + 主题色
- 内部数据行：mono 字体对齐

### 5.3 标签体系（三种级别）
| 类型 | 样式 | 用途 |
|------|------|------|
| 印章标 | 主题色 1.5px 边框 + 浅色底，2px 圆角 | 最强肯定信号（如"建议保留"） |
| 警示标 | warning 色 1px 边框 + 浅底 | 注意信息（如"可能不同版本"） |
| 中性标 | 灰边 + 灰字，mono 大写 | 元信息（扩展名、来源） |

### 5.4 筛选 Chips
3px 小圆角（非药丸形）、mono 字体；选中态 = 主题色文字 + 主题色边框 + 8% 浅底，不加粗阴影。

### 5.5 延迟浮现的行内操作
工具型产品行内操作按钮的黄金模式：

```css
.row .actions {
  opacity: 0; pointer-events: none;
  transition: opacity .18s var(--ease);
}
.row:hover .actions {
  opacity: 1; pointer-events: auto;
  transition-delay: .5s;   /* 用户需停留片刻才出现，防止视觉闪烁 */
}
```
移开鼠标立即消失（delay 只在 hover 态生效）。24px 方形小按钮，线性 SVG 图标，hover 变主题色。

### 5.6 底部操作栏
白底 + 顶部 3px 墨色粗线（锚定感）；数量用 serif 主题色大数字；按钮右置。

### 5.7 弹窗与浮层
- 遮罩 `rgba(28,28,30,.35)` + `blur(4px)`（浅色遮罩，不用深黑）
- 弹窗入场：`translateY(12px) → 0`，300ms，**不用 scale 弹跳**
- 标题一律 serif 18px

### 5.8 Toast（反转手法）
全站唯一的"反色"元素：墨底白字，在浅色海洋中天然跳脱，不需要彩色边框。

### 5.9 空状态
serif 大号符号（`✓` `□`）+ 主题色 30% 透明度 + 小号宽字距说明文字。

---

## 六、交互节奏

| 场景 | 时长 | 原则 |
|------|------|------|
| Tab/页面切换 | **≤180ms** | 400ms 会感到"肉"，180ms 即点即到 |
| hover 反馈 | 150~250ms | 背景色、边框色微调 |
| 展开/折叠 | 250~300ms | 给内容出现留一点仪式感 |
| 弹窗入场 | 300ms | 缓出，勿回弹 |
| 延迟呈现 | 500ms delay | hover 停留才出现的功能按钮 |

---

## 七、文案与图标

- **禁用 emoji** 作为界面图标（📁🔍⚙️⚠️🎉）。全部替换为：
  - 线性 SVG 图标（stroke 2~2.4，圆角线帽）
  - 排版化符号（`✓` `□` `▾` `×`）
  - 纯文字
- 按钮文案 2~4 字为宜，不用图标+文字堆叠
- 占位符可以有人情味：AI 输入框用 serif 斜体，像纸上批注

---

## 八、可直接复用的 CSS 骨架

```css
:root {
  --bg:#faf9f6; --bg-deep:#f4f2ec; --surface:#fff;
  --border:#e7e4dc; --border-strong:#d6d2c7;
  --text:#1c1c1e; --text-dim:#8a867e;
  --success:#6a8f6b; --warning:#b98a2f; --danger:#b5402f;
  --radius:8px; --radius-sm:5px;
  --shadow:0 1px 2px rgba(28,28,30,.04),0 4px 16px rgba(28,28,30,.05);
  --shadow-lg:0 2px 6px rgba(28,28,30,.06),0 16px 48px rgba(28,28,30,.12);
  --font:"Inter",-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
  --font-serif:"Source Serif 4","Noto Serif SC","Songti SC",Georgia,serif;
  --font-mono:"JetBrains Mono","SF Mono",Consolas,monospace;
  --ease:cubic-bezier(.22,.9,.32,1);
}
:root, :root[data-theme="cinnabar"] { --accent:#c8432b; --accent-deep:#a83824; --accent-soft:rgba(200,67,43,.08); }
:root[data-theme="indigo"]           { --accent:#2e4756; --accent-deep:#243945; --accent-soft:rgba(46,71,86,.09); }
:root[data-theme="pine"]             { --accent:#3f6b4f; --accent-deep:#33573f; --accent-soft:rgba(63,107,79,.09); }
```

主题切换器（HTML + JS，约 20 行）：

```html
<button class="theme-dot" data-theme-choice="cinnabar" style="--dot:#c8432b"></button>
<button class="theme-dot" data-theme-choice="indigo"   style="--dot:#2e4756"></button>
<button class="theme-dot" data-theme-choice="pine"     style="--dot:#3f6b4f"></button>
<script>
(function(){
  const KEY='app_theme';
  function apply(t){
    document.documentElement.setAttribute('data-theme',t);
    document.querySelectorAll('.theme-dot').forEach(d=>
      d.classList.toggle('active',d.dataset.themeChoice===t));
    localStorage.setItem(KEY,t);
  }
  document.querySelectorAll('.theme-dot').forEach(d=>
    d.addEventListener('click',()=>apply(d.dataset.themeChoice)));
  apply(localStorage.getItem(KEY)||'cinnabar');
})();
</script>
```

```css
.theme-dot { width:14px;height:14px;border-radius:50%;background:var(--dot);
  border:none;cursor:pointer;transition:.2s var(--ease); }
.theme-dot.active { box-shadow:0 0 0 2.5px var(--surface),0 0 0 4.5px var(--dot); }
```

---

## 九、检查清单（新组件落地前自问）

- [ ] 是否只用了 `var(--accent)` 引用主题色？
- [ ] 有没有偷偷引入渐变、发光、厚阴影？
- [ ] 数字和路径是否用了等宽字体 + `tabular-nums`？
- [ ] 标题是否该用衬线？
- [ ] 动效是否 ≤200ms（切换类）或带 500ms 延迟（浮现类）？
- [ ] 有没有 emoji 混进来？
- [ ] 灰色是暖灰（`#8a867e`）还是冷灰？冷灰一律换掉。

---

*v1.0 · 沉淀自「归置檔 · Dedup」v0.3 · 2026-07*
