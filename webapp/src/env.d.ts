import type { CastoriceBridgeApi } from "../electron/preload-api";

declare global {
  interface Window {
    /** preload contextBridge 暴露的桥接 API（electron/preload.ts）。 */
    castorice: CastoriceBridgeApi;
  }
}

/** Vite 静态资源（图片 import）。 */
declare module "*.jpeg" {
  const src: string;
  export default src;
}

declare module "*.png" {
  const src: string;
  export default src;
}

/** Vite 静态资源（图片 import）。 */
declare module "*.jpeg" {
  const src: string;
  export default src;
}

declare module "*.png" {
  const src: string;
  export default src;
}

export {};
