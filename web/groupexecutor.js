import { app } from "../../scripts/app.js";
import { ComfyWidgets } from "../../scripts/widgets.js";
import { api } from "../../scripts/api.js";
import { queueManager } from "./queue_utils.js";

class BaseNode extends LGraphNode {
    static defaultComfyClass = "BaseNode";
     constructor(title, comfyClass) {
        super(title);
        this.isVirtualNode = false;
        this.configuring = false;
        this.__constructed__ = false;
        this.widgets = this.widgets || [];
        this.properties = this.properties || {};
        this.comfyClass = comfyClass || this.constructor.comfyClass || BaseNode.defaultComfyClass;
         setTimeout(() => {
            this.checkAndRunOnConstructed();
        });
    }
    checkAndRunOnConstructed() {
        if (!this.__constructed__) {
            this.onConstructed();
        }
        return this.__constructed__;
    }
    onConstructed() {
        if (this.__constructed__) return false;
        this.type = this.type ?? undefined;
        this.__constructed__ = true;
        return this.__constructed__;
    }
    configure(info) {
        this.configuring = true;
        super.configure(info);
        for (const w of this.widgets || []) {
            w.last_y = w.last_y || 0;
        }
        this.configuring = false;
    }
    static setUp() {
        if (!this.type) {
            throw new Error(`Missing type for ${this.name}: ${this.title}`);
        }
        LiteGraph.registerNodeType(this.type, this);
        if (this._category) {
            this.category = this._category;
        }
    }
}

class GroupExecutorNode extends BaseNode {
    static type = "🎈GroupExecutor";
    static title = "🎈Group Executor";
    static category = "🎈LAOGOU/Group";
    static _category = "🎈LAOGOU/Group";
    
    constructor(title = GroupExecutorNode.title) {
        super(title, null);
        this.isVirtualNode = true;
        this.addProperty("groupCount", 1, "int");
        this.addProperty("groups", [], "array");
        this.addProperty("isExecuting", false, "boolean");
        this.addProperty("repeatCount", 1, "int");
        this.addProperty("delaySeconds", 0, "number");

        // 基础配置控件
        const groupCountWidget = ComfyWidgets["INT"](this, "groupCount", ["INT", {
            min: 1,
            max: 100,
            step: 1,
            default: 1
        }], app);
        
        const repeatCountWidget = ComfyWidgets["INT"](this, "repeatCount", ["INT", {
            min: 1,
            max: 100,
            step: 1,
            default: 1,
            label: "Repeat Count",
            tooltip: "执行重复次数"
        }], app);
        
        const delayWidget = ComfyWidgets["FLOAT"](this, "delaySeconds", ["FLOAT", {
            min: 0,
            max: 300,
            step: 0.1,
            default: 0,
            label: "Delay (s)",
            tooltip: "队列之间的延迟时间(秒)"
        }], app);

        // 调整控件顺序
        if (repeatCountWidget.widget && delayWidget.widget) {
            const widgets = [repeatCountWidget.widget, delayWidget.widget];
            widgets.forEach((widget, index) => {
                const widgetIndex = this.widgets.indexOf(widget);
                if (widgetIndex !== -1) {
                    const w = this.widgets.splice(widgetIndex, 1)[0];
                    this.widgets.splice(1 + index, 0, w);
                }
            });
        }

        // 控件回调
        groupCountWidget.widget.callback = (v) => {
            this.properties.groupCount = Math.max(1, Math.min(100, parseInt(v) || 1));
            this.updateGroupWidgets();
        };
        
        repeatCountWidget.widget.callback = (v) => {
            this.properties.repeatCount = Math.max(1, Math.min(100, parseInt(v) || 1));
        };
        
        delayWidget.widget.callback = (v) => {
            this.properties.delaySeconds = Math.max(0, Math.min(300, parseFloat(v) || 0));
        };

        // 核心功能按钮
        this.addWidget("button", "Execute Groups", "Execute", () => {
            this.executeGroups();
        });
        this.addWidget("button", "Cancel", "Cancel", () => {
            this.cancelExecution();
        });

        // ========== 配置管理相关控件 ==========
        // 配置名称输入框（用于保存到后端）
        this.addProperty("configName", "", "string");
        const configNameWidget = ComfyWidgets["STRING"](this, "configName", ["STRING", {
            default: "",
            label: "Config Name",
            tooltip: "配置名称（支持子目录，如 subdir/my_config）"
        }], app);
        
        // 配置管理按钮组
        this.addWidget("button", "💾 Save Config", "Save", () => {
            this.saveConfigToBackend();
        });
        
        this.addWidget("button", "📤 Export Config", "Export", () => {
            this.exportConfigToFile();
        });
        
        this.addWidget("button", "📥 Import Config", "Import", () => {
            this.importConfigFromFile();
        });
        
        // 加载后端配置的下拉框
        this.configComboWidget = this.addWidget(
            "combo",
            "Load Config",
            "",
            (v) => {
                if (v) this.loadConfigFromBackend(v);
            },
            { values: [] }
        );
        // 刷新配置列表
        this.refreshConfigList();

        // ========== 原有逻辑 ==========
        this.addProperty("isCancelling", false, "boolean");
        this.updateGroupWidgets();
        
        const self = this;
        app.canvas.onDrawBackground = (() => {
            const original = app.canvas.onDrawBackground;
            return function() {
                self.updateGroupList();
                return original?.apply(this, arguments);
            };
        })();
        
        this.originalTitle = title;
    }

    // ========== 配置导出/导入/保存/加载核心方法 ==========
    /**
     * 导出当前配置到本地文件
     */
    exportConfigToFile() {
        // 收集配置数据
        const config = {
            version: "1.0",
            groupCount: this.properties.groupCount,
            groups: this.properties.groups,
            repeatCount: this.properties.repeatCount,
            delaySeconds: this.properties.delaySeconds,
            createdAt: new Date().toISOString()
        };

        // 生成JSON文件并下载
        const blob = new Blob([JSON.stringify(config, null, 2)], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `group_executor_config_${new Date().getTime()}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        
        console.log("[GroupExecutor] 配置已导出到本地文件");
    }

    /**
     * 从本地文件导入配置
     */
    importConfigFromFile() {
        const input = document.createElement("input");
        input.type = "file";
        input.accept = ".json";
        
        input.onchange = (e) => {
            const file = e.target.files[0];
            if (!file) return;
            
            const reader = new FileReader();
            reader.onload = (event) => {
                try {
                    const config = JSON.parse(event.target.result);
                    
                    // 验证配置合法性
                    if (!config || typeof config !== "object") {
                        throw new Error("无效的配置文件格式");
                    }
                    
                    // 应用配置
                    this.properties.groupCount = Math.max(1, Math.min(100, parseInt(config.groupCount) || 1));
                    this.properties.groups = Array.isArray(config.groups) ? [...config.groups] : [];
                    this.properties.repeatCount = Math.max(1, Math.min(100, parseInt(config.repeatCount) || 1));
                    this.properties.delaySeconds = Math.max(0, Math.min(300, parseFloat(config.delaySeconds) || 0));
                    
                    // 更新UI
                    this.widgets.forEach(w => {
                        if (w.name === "groupCount") w.value = this.properties.groupCount;
                        if (w.name === "repeatCount") w.value = this.properties.repeatCount;
                        if (w.name === "delaySeconds") w.value = this.properties.delaySeconds;
                    });
                    
                    this.updateGroupWidgets();
                    this.setDirtyCanvas(true, true);
                    
                    console.log("[GroupExecutor] 配置已从本地文件导入");
                    app.ui.dialog.show("配置导入成功！");
                    
                } catch (error) {
                    console.error("[GroupExecutor] 导入配置失败:", error);
                    app.ui.dialog.show(`导入配置失败: ${error.message}`);
                }
            };
            
            reader.readAsText(file);
        };
        
        input.click();
    }

    /**
     * 刷新后端配置列表（支持子目录）
     */
    async refreshConfigList() {
        try {
            const response = await api.fetchApi("/group_executor/configs");
            const data = await response.json();
            
            if (data.status === "success" && Array.isArray(data.configs)) {
                // 提取带路径的配置名称，格式："子目录/配置名" 或 "配置名"
                const configNames = data.configs.map(c => c.path || c.name).sort();
                this.configComboWidget.options.values = ["", ...configNames];
                this.configComboWidget.value = "";
            }
        } catch (error) {
            console.error("[GroupExecutor] 刷新配置列表失败:", error);
        }
    }

    /**
     * 保存当前配置到后端（修复配置名称获取问题 + 支持子目录）
     */
    async saveConfigToBackend() {
        // 修复：从控件直接获取实时值（而非properties），避免同步延迟
        const configNameWidget = this.widgets.find(w => w.name === "configName");
        let configName = configNameWidget?.value?.trim() || "";
        
        if (!configName) {
            app.ui.dialog.show("请先输入配置名称！");
            return;
        }
        
        // 收集配置数据
        const config = {
            name: configName,
            groupCount: this.properties.groupCount,
            groups: this.properties.groups,
            repeatCount: this.properties.repeatCount,
            delaySeconds: this.properties.delaySeconds
        };
        
        try {
            const response = await api.fetchApi("/group_executor/configs", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify(config)
            });
            
            const data = await response.json();
            if (data.status === "success") {
                app.ui.dialog.show("配置保存成功！");
                this.refreshConfigList(); // 刷新配置列表
                // 同步更新properties中的配置名
                this.properties.configName = configName;
            } else {
                throw new Error(data.message || "保存失败");
            }
        } catch (error) {
            console.error("[GroupExecutor] 保存配置到后端失败:", error);
            app.ui.dialog.show(`保存配置失败: ${error.message}`);
        }
    }

    /**
     * 从后端加载配置（支持子目录路径解析）
     * @param {string} configPath 配置路径，如 "subdir/config1"
     */
    async loadConfigFromBackend(configPath) {
        if (!configPath) return;
        
        try {
            // 对路径进行编码，避免斜杠等字符导致请求错误
            const encodedPath = encodeURIComponent(configPath);
            const response = await api.fetchApi(`/group_executor/configs/${encodedPath}`);
            const config = await response.json();
            
            // 验证并应用配置
            if (config && typeof config === "object") {
                this.properties.groupCount = Math.max(1, Math.min(100, parseInt(config.groupCount) || 1));
                this.properties.groups = Array.isArray(config.groups) ? [...config.groups] : [];
                this.properties.repeatCount = Math.max(1, Math.min(100, parseInt(config.repeatCount) || 1));
                this.properties.delaySeconds = Math.max(0, Math.min(300, parseFloat(config.delaySeconds) || 0));
                this.properties.configName = config.name || "";
                
                // 更新UI控件值
                this.widgets.forEach(w => {
                    if (w.name === "groupCount") w.value = this.properties.groupCount;
                    if (w.name === "repeatCount") w.value = this.properties.repeatCount;
                    if (w.name === "delaySeconds") w.value = this.properties.delaySeconds;
                    if (w.name === "configName") w.value = this.properties.configName;
                });
                
                this.updateGroupWidgets();
                this.setDirtyCanvas(true, true);
                
                console.log(`[GroupExecutor] 已加载后端配置: ${configPath}`);
                app.ui.dialog.show(`配置 "${configPath}" 加载成功！`);
            }
        } catch (error) {
            console.error("[GroupExecutor] 加载后端配置失败:", error);
            app.ui.dialog.show(`加载配置失败: ${error.message}`);
        }
    }

    // ========== 原有方法 ==========
    getGroupNames() {
        return [...app.graph._groups].map(g => g.title).sort();
    }

    getGroupOutputNodes(groupName) {
        const group = app.graph._groups.find(g => g.title === groupName);
        if (!group) {
            console.warn(`[GroupExecutor] 未找到名为 "${groupName}" 的组`);
            return [];
        }
        const groupNodes = [];
        for (const node of app.graph._nodes) {
            if (!node || !node.pos) continue;
            if (LiteGraph.overlapBounding(group._bounding, node.getBounding())) {
                groupNodes.push(node);
            }
        }
        group._nodes = groupNodes;
        return this.getOutputNodes(group._nodes);
    }

    getOutputNodes(nodes) {
        return nodes.filter((n) => {
            return n.mode !== LiteGraph.NEVER &&
                   n.constructor.nodeData?.output_node === true;
        });
    }

    updateGroupWidgets() {
        const currentGroups = [...this.properties.groups];
        this.properties.groups = new Array(this.properties.groupCount).fill("").map((_, i) =>
            currentGroups[i] || ""
        );
        
        // 保留核心控件（排除配置相关控件避免被过滤）
        this.widgets = this.widgets.filter(w =>
            w.name === "groupCount" ||
            w.name === "repeatCount" ||
            w.name === "delaySeconds" ||
            w.name === "Execute Groups" ||
            w.name === "Cancel" ||
            w.name === "configName" ||
            w.name === "💾 Save Config" ||
            w.name === "📤 Export Config" ||
            w.name === "📥 Import Config" ||
            w.name === "Load Config"
        );
        
        const executeButton = this.widgets.find(w => w.name === "Execute Groups");
        const cancelButton = this.widgets.find(w => w.name === "Cancel");
        const saveConfigButton = this.widgets.find(w => w.name === "💾 Save Config");
        const exportConfigButton = this.widgets.find(w => w.name === "📤 Export Config");
        const importConfigButton = this.widgets.find(w => w.name === "📥 Import Config");
        const loadConfigCombo = this.widgets.find(w => w.name === "Load Config");

        // 移除临时按钮以便重新排序
        if (executeButton) this.widgets = this.widgets.filter(w => w.name !== "Execute Groups");
        if (cancelButton) this.widgets = this.widgets.filter(w => w.name !== "Cancel");
        if (saveConfigButton) this.widgets = this.widgets.filter(w => w.name !== "💾 Save Config");
        if (exportConfigButton) this.widgets = this.widgets.filter(w => w.name !== "📤 Export Config");
        if (importConfigButton) this.widgets = this.widgets.filter(w => w.name !== "📥 Import Config");
        if (loadConfigCombo) this.widgets = this.widgets.filter(w => w.name !== "Load Config");

        // 添加分组选择控件
        const groupNames = this.getGroupNames();
        for (let i = 0; i < this.properties.groupCount; i++) {
            const widget = this.addWidget(
                "combo",
                `Group #${i + 1}`,
                this.properties.groups[i] || "",
                (v) => {
                    this.properties.groups[i] = v;
                },
                {
                    values: groupNames
                }
            );
        }

        // 重新添加按钮和配置控件
        if (executeButton) this.widgets.push(executeButton);
        if (cancelButton) this.widgets.push(cancelButton);
        if (saveConfigButton) this.widgets.push(saveConfigButton);
        if (exportConfigButton) this.widgets.push(exportConfigButton);
        if (importConfigButton) this.widgets.push(importConfigButton);
        if (loadConfigCombo) this.widgets.push(loadConfigCombo);

        this.size = this.computeSize();
    }

    // ========== 关键修复：updateGroupList 方法 ==========
    updateGroupList() {
        const groups = this.getGroupNames();
        // 只更新分组选择的下拉框（Group #1、Group #2等），排除Load Config下拉框
        this.widgets.forEach(w => {
            // 仅更新名称以 "Group #" 开头的下拉框，跳过 Load Config 下拉框
            if (w.type === "combo" && w.name.startsWith("Group #")) {
                w.options.values = groups;
            }
        });
    }

    async delay(seconds) {
        if (seconds <= 0) return;
        return new Promise(resolve => setTimeout(resolve, seconds * 1000));
    }

    updateStatus(text) {
        this.title = `${this.originalTitle} - ${text}`;
        this.setDirtyCanvas(true, true);
    }

    resetStatus() {
        this.title = this.originalTitle;
        this.setDirtyCanvas(true, true);
    }

    async cancelExecution() {
        if (!this.properties.isExecuting) {
            console.warn('[GroupExecutor] 没有正在执行的任务');
            return;
        }
        try {
            this.properties.isCancelling = true;
            this.updateStatus("已取消");
            await api.interrupt();
            setTimeout(() => this.resetStatus(), 2000);
        } catch (error) {
            console.error('[GroupExecutor] 取消执行时出错:', error);
            this.updateStatus(`取消失败: ${error.message}`);
        }
    }

    async executeGroups() {
        if (this.properties.isExecuting) {
            console.warn('[GroupExecutor] 已有执行任务在进行中');
            return;
        }
        this.properties.isExecuting = true;
        this.properties.isCancelling = false;
        const totalSteps = this.properties.repeatCount * this.properties.groupCount;
        let currentStep = 0;
        try {
            for (let repeat = 0; repeat < this.properties.repeatCount; repeat++) {
                for (let i = 0; i < this.properties.groupCount; i++) {
                    if (this.properties.isCancelling) {
                        console.log('[GroupExecutor] 执行被用户取消');
                        await api.interrupt();
                        this.updateStatus("已取消");
                        setTimeout(() => this.resetStatus(), 2000);
                        return;
                    }
                    const groupName = this.properties.groups[i];
                    if (!groupName) continue;
                    currentStep++;
                    this.updateStatus(
                        `${currentStep}/${totalSteps} - ${groupName}`
                    );
                    const outputNodes = this.getGroupOutputNodes(groupName);
                    if (outputNodes && outputNodes.length > 0) {
                        try {
                            const nodeIds = outputNodes.map(n => n.id);
                            try {
                                if (this.properties.isCancelling) {
                                    return;
                                }
                                await queueManager.queueOutputNodes(nodeIds);
                                await this.waitForQueue();
                            } catch (queueError) {
                                if (this.properties.isCancelling) {
                                    return;
                                }
                                console.warn(`[GroupExecutorSender] 队列执行失败，使用默认方式:`, queueError);
                                for (const n of outputNodes) {
                                    if (this.properties.isCancelling) {
                                        return;
                                    }
                                    if (n.triggerQueue) {
                                        await n.triggerQueue();
                                        await this.waitForQueue();
                                    }
                                }
                            }
                            if (i < this.properties.groupCount - 1) {
                                if (this.properties.isCancelling) {
                                    return;
                                }
                                this.updateStatus(
                                    `等待 ${this.properties.delaySeconds}s...`
                                );
                                await this.delay(this.properties.delaySeconds);
                            }
                        } catch (error) {
                            console.error(`[GroupExecutor] 执行组 ${groupName} 时发生错误:`, error);
                            throw error;
                        }
                    }
                }
                if (repeat < this.properties.repeatCount - 1) {
                    if (this.properties.isCancelling) {
                        return;
                    }
                    await this.delay(this.properties.delaySeconds);
                }
            }
            if (!this.properties.isCancelling) {
                this.updateStatus("完成");
                setTimeout(() => this.resetStatus(), 2000);
            }
        } catch (error) {
            console.error('[GroupExecutor] 执行错误:', error);
            this.updateStatus(`错误: ${error.message}`);
            app.ui.dialog.show(`执行错误: ${error.message}`);
        } finally {
            this.properties.isExecuting = false;
            this.properties.isCancelling = false;
        }
    }

    async getQueueStatus() {
        try {
            const response = await fetch('/queue');
            const data = await response.json();
            return {
                isRunning: data.queue_running.length > 0,
                isPending: data.queue_pending.length > 0,
                runningCount: data.queue_running.length,
                pendingCount: data.queue_pending.length,
                rawRunning: data.queue_running,
                rawPending: data.queue_pending
            };
        } catch (error) {
            console.error('[GroupExecutor] 获取队列状态失败:', error);
            return {
                isRunning: false,
                isPending: false,
                runningCount: 0,
                pendingCount: 0,
                rawRunning: [],
                rawPending: []
            };
        }
    }

    async waitForQueue() {
        return new Promise((resolve, reject) => {
            const checkQueue = async () => {
                try {
                    const status = await this.getQueueStatus();
                    if (!status.isRunning && !status.isPending) {
                        setTimeout(resolve, 100);
                        return;
                    }
                    setTimeout(checkQueue, 500);
                } catch (error) {
                    console.warn(`[GroupExecutor] 检查队列状态失败:`, error);
                    setTimeout(checkQueue, 500);
                }
            };
            checkQueue();
        });
    }

    computeSize() {
        const widgetHeight = 28;
        const padding = 4;
        const width = 200;
        // 增加配置控件的高度
        const height = (this.properties.groupCount + 8) * widgetHeight + padding * 2;
        return [width, height];
    }

    static setUp() {
        LiteGraph.registerNodeType(this.type, this);
        this.category = this._category;
    }

    serialize() {
        const data = super.serialize();
        data.properties = {
            ...data.properties,
            groupCount: parseInt(this.properties.groupCount) || 1,
            groups: [...this.properties.groups],
            isExecuting: this.properties.isExecuting,
            repeatCount: parseInt(this.properties.repeatCount) || 1,
            delaySeconds: parseFloat(this.properties.delaySeconds) || 0,
            configName: this.properties.configName || "" // 序列化配置名称
        };
        return data;
    }

    configure(info) {
        super.configure(info);
        if (info.properties) {
            this.properties.groupCount = parseInt(info.properties.groupCount) || 1;
            this.properties.groups = info.properties.groups ? [...info.properties.groups] : [];
            this.properties.isExecuting = info.properties.isExecuting ?? false;
            this.properties.repeatCount = parseInt(info.properties.repeatCount) || 1;
            this.properties.delaySeconds = parseFloat(info.properties.delaySeconds) || 0;
            this.properties.configName = info.properties.configName || ""; // 加载配置名称
        }
        this.widgets.forEach(w => {
            if (w.name === "groupCount") {
                w.value = this.properties.groupCount;
            } else if (w.name === "repeatCount") {
                w.value = this.properties.repeatCount;
            } else if (w.name === "delaySeconds") {
                w.value = this.properties.delaySeconds;
            } else if (w.name === "configName") { // 设置配置名称控件值
                w.value = this.properties.configName;
            }
        });
        if (!this.configuring) {
            this.updateGroupWidgets();
            this.refreshConfigList(); // 刷新配置列表
        }
    }
}

app.registerExtension({
    name: "GroupExecutor",
    registerCustomNodes() {
        GroupExecutorNode.setUp();
    }
});
