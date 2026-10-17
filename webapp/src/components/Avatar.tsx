import avatarUrl from "../assets/castorice-avatar.jpeg?url";

/**
 * 角色头像（AI 左侧带头像 / 欢迎页居中大头像）。
 * 素材：Image/CastoriceAvatar.jpeg（构建期拷进 assets，避免 file:// 越界）。
 */
export function Avatar({ size = 36 }: { size?: number }) {
  return (
    <div
      className="shrink-0 overflow-hidden rounded-full border"
      style={{
        width: size,
        height: size,
        background: "var(--c-avatar-bg)",
        borderColor: "var(--c-avatar-border)",
      }}
    >
      <img src={avatarUrl} alt="" className="h-full w-full object-cover" draggable={false} />
    </div>
  );
}
