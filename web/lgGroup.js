import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";

// 存储不同 link_id 对应的最新数据（新增 audio 缓存）
const linkDataCache = {
    img: {},
    video: {},
    string: {},
    value: {},
    audio: {}, // 新增：音频缓存
    latent: {} // 新增：Latent 缓存
};

// 需要锁定尺寸的节点类型
const SIZE_LOCKED_TYPES = new Set([
    "LG_ValueReceiver",
    "LG_StringReceiver",
    "LG_VideoReceiver",
    "LG_LatentReceiver",
    "LG_audioReceiver",
]);

// 通用的处理接收消息的函数（扩展支持 audio 类型）
function handleReceiveMessage(event, type, widgetName) {
    const data = event.detail;
    const linkId = data.link_id;
    
    console.log(`[LG Frontend] 收到 ${type} 消息 (Link ID: ${linkId})`, data);

    // 1. 缓存数据（新增 audio 缓存逻辑）
    if (type === 'img') linkDataCache.img[linkId] = data.images;
    else if (type === 'video') linkDataCache.video[linkId] = data.videos;
    else if (type === 'string') linkDataCache.string[linkId] = data.strings;
    else if (type === 'audio') linkDataCache.audio[linkId] = data.audios; // 新增
    else if (type === 'latent') linkDataCache.latent[linkId] = data.latents; // 新增

    // 2. 查找画布上所有对应的接收节点并填充
    // 注意：这里的节点类型必须与 Python 代码中类名完全一致
    const nodeTypes = {
        img: "LG_ImageReceiver",
        video: "LG_VideoReceiver",
        string: "LG_StringReceiver",
        audio: "LG_audioReceiver", // 新增：音频接收节点类型映射
        latent: "LG_LatentReceiver" // 新增：Latent 接收节点
    };

    for (const node of app.graph._nodes) {
        if (node.type === nodeTypes[type]) {
            // 找到节点的 link_id widget
            const linkIdWidget = node.widgets?.find(w => w.name === "link_id");
            // 找到目标输入 widget (image/video/string/audio)
            const targetWidget = node.widgets?.find(w => w.name === widgetName);

            if (linkIdWidget && targetWidget && linkIdWidget.value === linkId) {
                // 提取文件名并用逗号连接（适配 audio 类型的 data.audios 字段）
                let files = [];
                if (type === 'img') files = data.images;
                else if (type === 'video') files = data.videos;
                else if (type === 'string') files = data.strings;
                else if (type === 'audio') files = data.audios; // 新增
                else if (type === 'latent') files = data.latents; // 新增
                
                const filenames = files.map(f => f.filename).join(",");
                
                console.log(`[LG Frontend] 自动填充节点 ${node.id} 的 ${widgetName}: ${filenames}`);
                targetWidget.value = filenames;
                
                // 仅在需要时重绘，但锁定尺寸不变
                setNodeSizeLocked(node);
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
                // 锁定尺寸，不随内容增多而撑大节点
                setNodeSizeLocked(node);
            }
        }
    }
}

/**
 * 设置节点尺寸，但锁定为初始尺寸，防止内容撑大。
 * 初始尺寸在节点第一次渲染后记录到 node._lockedSize。
 */
function setNodeSizeLocked(node) {
    if (!node._lockedSize) {
        // 还没记录过：用当前尺寸作为基准锁定
        node._lockedSize = [node.size[0], node.size[1]];
    }
    node.setSize(node._lockedSize);
    app.canvas.setDirty(true);
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
                // 清空后也锁定尺寸
                setNodeSizeLocked(node);
            }
        }
    }
}

app.registerExtension({
    name: "Comfy.LG_GroupExecutor",
    init() {
        console.log("[LG Frontend] 初始化");
        // 监听后端发来的消息（新增 audio-send 监听）
        api.addEventListener("img-send", (e) => handleReceiveMessage(e, "img", "image"));
        api.addEventListener("video-send", (e) => handleReceiveMessage(e, "video", "video"));
        api.addEventListener("string-send", (e) => handleReceiveMessage(e, "string", "string"));
        api.addEventListener("audio-send", (e) => handleReceiveMessage(e, "audio", "audio")); // 新增：监听音频消息
        api.addEventListener("latent-send", (e) => handleReceiveMessage(e, "latent", "latent"));
        api.addEventListener("value-send-accumulate", handleValueMessage);
        api.addEventListener("value-clear-accumulate", handleClearValue);
    },
    // 节点创建时记录初始尺寸，作为锁定基准
    nodeCreated(node) {
        if (SIZE_LOCKED_TYPES.has(node.type)) {
            // 等待一帧，确保节点已完成初始布局再记录尺寸
            requestAnimationFrame(() => {
                if (!node._lockedSize) {
                    node._lockedSize = [node.size[0], node.size[1]];
                    console.log(`[LG Frontend] 锁定节点 ${node.id} (${node.type}) 尺寸: ${node._lockedSize}`);
                }
            });
        }
    }
});
