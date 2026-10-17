/** Vite 静态资源（图片 import 的模块声明，含 ?url 形态）。 */
declare module "*.jpeg" {
  const src: string;
  export default src;
}

declare module "*.jpeg?url" {
  const src: string;
  export default src;
}

declare module "*.png" {
  const src: string;
  export default src;
}

declare module "*.png?url" {
  const src: string;
  export default src;
}
