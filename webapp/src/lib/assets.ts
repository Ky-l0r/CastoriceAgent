/**
 * 静态资源 URL 解析。
 *
 * 为什么需要这层：vite.config.ts 设了 base: "./"——生产环境 Electron 用
 * file:// 加载 dist/renderer/index.html，绝对路径 "/castorice-assets/x.png"
 * 会被解析成磁盘根目录（file:///castorice-assets/x.png）而 404。
 * 所有静态资源必须经 BASE_URL 拼接才能同时适配 dev（http://）与
 * 生产（file://）。
 */

const BASE = import.meta.env.BASE_URL || "./";

/** 拼一个 public/ 下静态资源的可加载 URL。 */
export function assetUrl(path: string): string {
  const clean = path.replace(/^\/+/, "");
  return BASE.endsWith("/") ? BASE + clean : BASE + "/" + clean;
}

/** 角色头像（public/castorice-assets 下的部署副本）。 */
export const AVATAR_URL = assetUrl("castorice-assets/CastoriceAvatar.jpeg");

/** 欢迎页 Q 版贴图。 */
export const WELCOME_CHIBI_URL = assetUrl("castorice-assets/welcome-chibi.png");

/**
 * 表情包文件名 -> 可加载 URL。
 *
 * 后端 emoji 事件的 path 是宿主机绝对路径（如 D:\...\Image\表情包\喜欢.png）。
 * 界面不该依赖宿主机的目录结构：只取文件名，拼到打包内置的贴纸目录上。
 * 内置贴纸随 public/castorice-assets/表情包 一起发布，文件名与后端同名。
 */
export function stickerUrl(fileName: string): string {
  if (!fileName) return "";
  const base = fileName.split(/[\\/]/).pop() || "";
  if (!base) return "";
  return assetUrl("castorice-assets/表情包/" + base);
}
