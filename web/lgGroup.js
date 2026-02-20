import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";

// 存储不同 link_id 对应的最新数据
const linkDataCache = {
    img: {},
    video: {},
    string: {},
    value: {}
};

// 通用的处理接收消息的函数
function handleReceiveMessage(event, type, widgetName) {
    const data = event.detail;
    const linkId = data.link_id;
    
    console.log(`[LG Frontend] 收到 ${type} 消息 (Link ID: ${linkId})`, data);

    // 1. 缓存数据
    if (type === 'img') linkDataCache.img[linkId] = data.images;
    else if (type === 'video') linkDataCache.video[linkId] = data.videos;
    else if (type === 'string') linkDataCache.string[linkId] = data.strings;

    // 2. 查找画布上所有对应的接收节点并填充
    // 注意：这里的 "LG_StringReceiver" 等必须与你 Python 代码中类名完全一致
    const nodeTypes = {
        img: "LG_ImageReceiver",
        video: "LG_VideoReceiver",
        string: "LG_StringReceiver"
    };

    for (const node of app.graph._nodes) {
        if (node.type === nodeTypes[type]) {
            // 找到节点的 link_id widget
            const linkIdWidget = node.widgets?.find(w => w.name === "link_id");
            // 找到目标输入 widget (image, video, or string)
            const targetWidget = node.widgets?.find(w => w.name === widgetName);

            if (linkIdWidget && targetWidget && linkIdWidget.value === linkId) {
                // 提取文件名并用逗号连接
                const files = (type === 'img' ? data.images : (type === 'video' ? data.videos : data.strings));
                const filenames = files.map(f => f.filename).join(",");
                
                console.log(`[LG Frontend] 自动填充节点 ${node.id} 的 ${widgetName}: ${filenames}`);
                targetWidget.value = filenames;
                
                // 触发节点尺寸重绘（防止文字显示不全）
                node.setSize(node.computeSize());
            }
        }
    }
}

// 处理 Value 接收的逻辑 (略有不同，因为是换行分隔)
function handleValueMessage(event) {
    const data = event.detail;
    const linkId = data.link_id;
    
    console.log(`[LG Frontend] 收到 Value 消息 (Link ID: ${linkId})`, data);

    if (!linkDataCache.value[linkId]) {
        linkDataCache.value[linkId] = [];
    }
    // 累积模式：添加新值
    if (data.value && !linkDataCache.value[linkId].includes(data.value)) {
        linkDataCache.value[linkId].push(data.value);
    }

    // 查找节点并填充
    for (const node of app.graph._nodes) {
        if (node.type === "LG_ValueReceiver") {
            const linkIdWidget = node.widgets?.find(w => w.name === "link_id");
            const targetWidget = node.widgets?.find(w => w.name === "value");
            const accWidget = node.widgets?.find(w => w.name === "accumulate");

            if (linkIdWidget && targetWidget && linkIdWidget.value === linkId) {
                // 如果是累积模式用缓存，否则只用当前值
                if (accWidget && accWidget.value) {
                    targetWidget.value = linkDataCache.value[linkId].join("\n");
                } else {
                    targetWidget.value = data.value || "";
                }
                node.setSize(node.computeSize());
            }
        }
    }
}

// 处理清空累积
function handleClearValue(event) {
    const data = event.detail;
    if (data.link_id === -1) {
        linkDataCache.value = {};
        console.log("[LG Frontend] 清空所有累积值");
    } else {
        linkDataCache.value[data.link_id] = [];
        console.log(`[LG Frontend] 清空 Link ID ${data.link_id} 的累积值`);
    }
    
    // 清空界面上的输入框
    for (const node of app.graph._nodes) {
        if (node.type === "LG_ValueReceiver") {
            const linkIdWidget = node.widgets?.find(w => w.name === "link_id");
            const targetWidget = node.widgets?.find(w => w.name === "value");
            if (linkIdWidget && targetWidget && (data.link_id === -1 || linkIdWidget.value === data.link_id)) {
                targetWidget.value = "";
            }
        }
    }
}

app.registerExtension({
    name: "Comfy.LG_GroupExecutor",
    init() {
        console.log("[LG Frontend] 初始化");
        // 监听后端发来的消息
        api.addEventListener("img-send", (e) => handleReceiveMessage(e, "img", "image"));
        api.addEventListener("video-send", (e) => handleReceiveMessage(e, "video", "video"));
        api.addEventListener("string-send", (e) => handleReceiveMessage(e, "string", "string"));
        api.addEventListener("value-send-accumulate", handleValueMessage);
        api.addEventListener("value-clear-accumulate", handleClearValue);
    },
    // 当节点被创建时的钩子（可选，用于设置默认值或样式）
    nodeCreated(node) {
        // 可以在这里给特定节点添加一些前端交互逻辑
    }
});