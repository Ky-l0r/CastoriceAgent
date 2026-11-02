/**
 * 表情包标记的工具函数。
 *
 * 用户发表情包时，输入框里插入的是 `[表情:名称]` 文本标记（模型能读到
 * 语义，也不需要 vision）。界面展示层要把它还原成图片，否则用户会在
 * 自己的气泡里看到一串「[表情:喜欢]」——这正是此前的缺陷。
 *
 * 正则与后端 ai.py 的 EMOJI_TAG_RE 保持一致（同一套标记语法）。
 */

/** 与 ai.py EMOJI_TAG_RE 对齐的标记正则（带 g，可重复使用 matchAll/replace）。 */
export const STICKER_TAG_RE =
  /[\[【]\s*(?:表情包?|贴纸|emoji|sticker)\s*[:：]\s*([^\]】\n]{1,24}?)\s*[\]】]/gi;

export interface StickerSplit {
  /** 剥掉表情标记后的正文（可能为空字符串——用户只发了一个表情）。 */
  text: string;
  /** 按出现顺序的贴纸名（已去重？不去重：连点两次就是两个表情）。 */
  stickers: string[];
}

/** 把一段正文拆成「纯文本 + 贴纸名列表」。没有标记时原样返回。 */
export function splitStickers(text: string): StickerSplit {
  const source = text ?? "";
  const stickers: string[] = [];
  STICKER_TAG_RE.lastIndex = 0;
  const plain = source
    .replace(STICKER_TAG_RE, (_match, name: string) => {
      const label = String(name ?? "").trim();
      if (label) stickers.push(label);
      return "";
    })
    .replace(/[ \t]{2,}/g, " ")
    .replace(/[ \t]+\n/g, "\n")
    .trim();
  return { text: plain, stickers };
}

/** 名称 -> 标记文本（发送给后端时把图片选择还原成模型可读的标记）。 */
export function stickerTag(name: string): string {
  return "[表情:" + name + "]";
}
