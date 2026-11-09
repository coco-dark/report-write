import { createApp } from "vue";
import Antd from "ant-design-vue";
import "ant-design-vue/dist/reset.css";

import "./theme.css";
import App from "./App.vue";
import UiAutoComplete from "./components/UiAutoComplete.vue";
import UiTree from "./components/UiTree.vue";

const app = createApp(App);
app.use(Antd);
app.component("UiTree", UiTree);
app.component("UiAutoComplete", UiAutoComplete);
app.mount("#app");
