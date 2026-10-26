---
name: LIVO 录包回看
description: 中文本地离线 LiDAR 与同步相机录包播放器
colors:
  paper: "#f3f2ed"
  surface: "#e9eae3"
  ink: "#26352f"
  muted: "#58675e"
  line: "#d0d6cc"
  accent: "#226343"
  canvas: "#101a17"
  focus: "#4f906b"
  error-bg: "#f8e9dc"
  error-text: "#713d1e"
typography:
  body:
    fontFamily: 'system-ui, "Noto Sans CJK SC", sans-serif'
    fontSize: "14px"
  title:
    fontSize: "15px"
    fontWeight: 650
  label:
    fontSize: "12px"
rounded:
  control: "6px"
spacing:
  control-gap: "8px"
  panel-inline: "18px"
  page-inline: "24px"
components:
  button:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    height: "34px"
    padding: "0 11px"
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "#fff"
    rounded: "{rounded.control}"
    height: "34px"
---

# Design System: LIVO 录包回看

## Overview

仅适用于 `viewer/` 的中文本地离线录包播放器。以真实 WebGL 三维点云、同一时间轴上的相机图像和清晰的播放状态为中心，采用紧凑、平面的原生控件界面。本文提取当前实现，不建立额外品牌概念；代码来源为 `viewer/style.css`、`viewer/index.html` 和 `viewer/app.js`。

**Key Characteristics:**
- 中文操作文案与本地资源。
- 深色三维视口、浅色工具和相机区域。
- 共享时间轴与明确的缺帧、空数据和错误提示。

## Colors

前置 token 是当前 CSS 色值的规范记录；`viewer/style.css` 是实现来源。绿色 accent 用于播放主操作和原生范围控件；paper 是页面及控件底色，surface 承载相机区域。ink、muted、line 分别用于正文、次要说明及区域分隔。canvas 为三维视口底色。focus 用于键盘焦点；error-bg 与 error-text 用于可读的错误信息。三维坐标轴保留红、绿、蓝区分；点云着色表达数据，不承担按钮状态。

## Typography

使用系统字体和中文无衬线后备，不加载外部字体。正文采用 body；面板标题采用 title；工具、时间差和状态说明采用 label。品牌字号为桌面 23px、窄屏 20px。帧数、时间和点数使用等宽数字，避免播放时布局跳动。

## Layout

桌面主区域使用 `minmax(0, 1.9fr) minmax(280px, 1fr)` 两列：左侧点云，右侧相机。顶部文件选择、底部共享播放区与内容区由细线分隔。常规顶部高度 68px，面板工具栏 56px。

宽度不超过 1100px 时工具栏换行；不超过 680px 时点云与相机纵向排列，顶部录包选择独占一行，底部播放栏保持粘性。页面最小宽度 320px；移动点云区域至少 330px，相机区域至少 250px。相机图像保持原始比例完整显示。

## Elevation & Depth

界面无阴影；通过浅色区域、细边线和深色视口区分结构。三维深度由真实透视点云、网格和坐标轴表达，不用装饰性卡片或模糊叠层。

## Shapes

按钮、选择框和视口错误框使用 control 圆角。内容面板为平直矩形；按钮使用细边框。范围输入保留浏览器原生形态。

## Components

- 文件选择：原生 select，载入期间禁用；长路径放在 title 中。
- 播放控件：上一帧、播放/暂停、下一帧、速度和逐帧范围滑块共享一个时间轴。主按钮使用 accent，载入时禁用。按钮文字和 `aria-pressed` 反映播放状态。
- 三维视口：鼠标旋转、右键或 Shift 拖动平移、滚轮缩放；保留复位、着色和点大小控件。WebGL 不可用时显示文字错误。
- 相机状态：明确区分没有相机流、当前时间无匹配图像和解码错误。缺帧时隐藏旧图像，解码错误时点云继续播放；匹配时显示记录时差。
- 可访问性：原生控件、中文可访问名称、可见焦点、错误 `role="alert"`。空格播放/暂停，左右箭头逐帧；焦点位于表单控件时保留原生键盘行为。当前三维视角操作依赖指针，勿宣称完整键盘视角控制。
- 反馈：按钮与选择框背景过渡 0.12s，减少动态效果偏好下关闭过渡；禁用按钮半透明并显示等待光标。

## Do's and Don'ts

- Do 保持点云和相机使用同一个回放时间轴。
- Do 保留空数据、图像时间缺口和错误的中文文字反馈。
- Do 使用系统字体、原生控件和可见键盘焦点。
- Don't 用上一张图像填充当前时间缺口。
- Don't 加入外部字体、图片、图标包或装饰性阴影。
- Don't 将本文件的 viewer 规则扩展到整个算法工程。
