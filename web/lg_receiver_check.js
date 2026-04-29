import { app } from "../../scripts/app.js";

// 所有接收节点对应的 widget 名称
const RECEIVER_WIDGET_MAP = {
    "LG_ImageReceiver":  "image",
    "LG_VideoReceiver":  "video",
    "LG_StringReceiver": "string",
    "LG_audioReceiver":  "audio",
    "LG_LatentReceiver": "latent",
};

const DOT_RADIUS = 6;
const DOT_MARGIN = 8;

/**
 * 检查一组文件名（逗号分隔）是否全部存在
 * @returns {Promise<boolean|null>} true=全存在 false=有缺失 null=未填
 */
async function checkFiles(value) {
    if (!value || value.trim() === "") return null;
    const files = value.split(",").map(f => f.trim()).filter(Boolean);
    if (files.length === 0) return null;
    try {
        const checks = await Promise.all(
            files.map(f =>
                fetch(`/laogou/check_temp_file?filename=${encodeURIComponent(f)}`)
                    .then(r => r.json())
                    .catch(() => ({ exists: false }))
            )
        );
        return checks.every(r => r.exists);
    } catch (_) {
        return null;
    }
}

app.registerExtension({
    name: "LG.ReceiverFileChecker",

    async nodeCreated(node) {
        const widgetName = RECEIVER_WIDGET_MAP[node.comfyClass];
        if (!widgetName) return;

        const widget = node.widgets?.find(w => w.name === widgetName);
        if (!widget) return;

        // 初始状态: null = 未填/未检查
        node._lgFileStatus = null;

        const doCheck = async () => {
            node._lgFileStatus = await checkFiles(widget.value);
            node.setDirtyCanvas(true, true);
        };

        // 当 widget 值变化时触发检查
        const origCb = widget.callback;
        widget.callback = function (value, ...rest) {
            doCheck();
            return origCb?.call(this, value, ...rest);
        };

        // 节点被选中时检查
        const origSelected = node.onSelected?.bind(node);
        node.onSelected = function () {
            doCheck();
            origSelected?.();
        };

        // 绘制状态圆点
        const origDrawFg = node.onDrawForeground?.bind(node);
        node.onDrawForeground = function (ctx) {
            origDrawFg?.(ctx);

            const status = node._lgFileStatus;
            if (status === null) return; // 未填或没检查，不画

            const x = node.size[0] - DOT_RADIUS - DOT_MARGIN;
            const y = -(LiteGraph.NODE_TITLE_HEIGHT / 2); // 标题栏中间

            ctx.save();

            // 发光
            ctx.shadowColor = status ? "#00ff66" : "#ff2222";
            ctx.shadowBlur = 6;

            ctx.beginPath();
            ctx.arc(x, y, DOT_RADIUS, 0, Math.PI * 2);
            ctx.fillStyle = status ? "#00cc44" : "#ff3333";
            ctx.fill();

            ctx.shadowBlur = 0;
            ctx.strokeStyle = "rgba(255,255,255,0.5)";
            ctx.lineWidth = 1;
            ctx.stroke();

            // 缺失时叠加淡红底色
            if (!status) {
                ctx.globalAlpha = 0.07;
                ctx.fillStyle = "#ff0000";
                ctx.fillRect(
                    0,
                    LiteGraph.NODE_TITLE_HEIGHT,
                    node.size[0],
                    node.size[1] - LiteGraph.NODE_TITLE_HEIGHT
                );
                ctx.globalAlpha = 1.0;
            }

            ctx.restore();
        };

        // 鼠标悬停提示
        const origGetTitle = node.getTitle?.bind(node);
        node.getTitle = function () {
            const base = origGetTitle?.() ?? node.title ?? node.comfyClass;
            if (node._lgFileStatus === false) {
                return `⚠️ ${base} [文件缺失]`;
            }
            return base;
        };

        // 延迟触发首次检查（确保 widget 值已加载）
        setTimeout(doCheck, 400);
    },

    /**
     * 加载工作流后，批量检查所有接收节点
     */
    async setup() {
        const origLoadGraph = app.loadGraphData?.bind(app);
        if (!origLoadGraph) return;
        app.loadGraphData = async function (...args) {
            const result = await origLoadGraph(...args);
            setTimeout(() => {
                for (const node of app.graph._nodes) {
                    if (node.comfyClass in RECEIVER_WIDGET_MAP) {
                        node.onSelected?.();
                    }
                }
            }, 800);
            return result;
        };
    },
});
