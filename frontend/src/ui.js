import { createVNode, defineComponent, h, ref } from "vue";
import { Input, Modal, message } from "ant-design-vue";

function durationSec(ms) {
  if (ms == null) return 3;
  if (ms === 0) return 0;
  return ms > 20 ? ms / 1000 : ms;
}

function emitToast(type, content, duration) {
  const fn = message[type] || message.info;
  const hide = fn(content, durationSec(duration));
  return {
    close() {
      if (typeof hide === "function") hide();
    },
  };
}

/** 兼容原 ElMessage 调用面，内部走 ant-design-vue message。 */
export const ElMessage = Object.assign(
  (opts) => {
    if (typeof opts === "string") return emitToast("info", opts);
    const type = opts?.type || "info";
    return emitToast(type, opts?.message || "", opts?.duration);
  },
  {
    success: (msg) => emitToast("success", msg),
    error: (msg) => emitToast("error", msg),
    warning: (msg) => emitToast("warning", msg),
    info: (msg) => emitToast("info", msg),
    loading: (msg, duration = 0) => emitToast("loading", msg, duration),
  }
);

function confirm(content, title, options = {}) {
  const danger = options.type === "warning" || options.type === "error" || options.type === "danger";
  return new Promise((resolve, reject) => {
    Modal.confirm({
      title: title || "确认",
      content,
      okText: options.confirmButtonText || "确定",
      cancelText: options.cancelButtonText || "取消",
      okType: danger ? "danger" : "primary",
      centered: true,
      onOk: () => resolve("confirm"),
      onCancel: () => reject("cancel"),
    });
  });
}

function prompt(content, title, options = {}) {
  const valueRef = ref(options.inputValue || "");
  const PromptBody = defineComponent({
    setup() {
      return () =>
        h("div", { class: "ui-prompt" }, [
          content ? h("p", { style: "margin: 0 0 10px" }, String(content)) : null,
          h(Input, {
            value: valueRef.value,
            placeholder: options.inputPlaceholder || "",
            autofocus: true,
            allowClear: true,
            onChange: (e) => {
              valueRef.value = e?.target?.value ?? "";
            },
            "onUpdate:value": (v) => {
              valueRef.value = v == null ? "" : String(v);
            },
          }),
        ]);
    },
  });
  return new Promise((resolve, reject) => {
    Modal.confirm({
      title: title || "请输入",
      content: createVNode(PromptBody),
      okText: options.confirmButtonText || "确定",
      cancelText: options.cancelButtonText || "取消",
      centered: true,
      onOk: () => resolve({ value: valueRef.value }),
      onCancel: () => reject("cancel"),
    });
  });
}

export const ElMessageBox = { confirm, prompt, alert: confirm };

export const toast = ElMessage;

export const antdTheme = {
  token: {
    colorPrimary: "#4e6ef2",
    colorPrimaryHover: "#3f5de0",
    borderRadius: 10,
    fontFamily: '"PingFang SC", "Segoe UI", "Helvetica Neue", Arial, sans-serif',
  },
};
