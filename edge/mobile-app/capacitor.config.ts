import type { CapacitorConfig } from "@capacitor/cli";

const config: CapacitorConfig = {
  appId: "com.catfish.mobile",
  appName: "小鲶",
  webDir: "dist",
  ios: {
    // 键盘弹出时页面跟着缩, 输入框不被挡
    contentInset: "never",
  },
  plugins: {
    // 登录换令牌走原生网络请求: App 页面的来源是 capacitor://localhost, 浏览器式的 fetch 会被跨域拦
    CapacitorHttp: { enabled: true },
    PushNotifications: { presentationOptions: ["badge", "sound", "alert"] },
  },
};

export default config;
