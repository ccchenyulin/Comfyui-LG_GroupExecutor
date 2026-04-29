from server import PromptServer
import os
import sys
import torch
import numpy as np
from PIL import Image
import folder_paths
import random
from nodes import SaveImage
import json
from comfy.cli_args import args
from PIL.PngImagePlugin import PngInfo
import time
import cv2  # 视频处理所需

CATEGORY_TYPE = "🎈LAOGOU/Group"
class AnyType(str):
    """用于表示任意类型的特殊类，在类型比较时总是返回相等"""
    def __eq__(self, _) -> bool:
        return True

    def __ne__(self, __value: object) -> bool:
        return False

any_typ = AnyType("*")

class LG_ImageSender:
    def __init__(self):
        self.output_dir = folder_paths.get_temp_directory()
        self.type = "temp"
        self.compress_level = 1
        self.accumulated_results = []  
        
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "images": ("IMAGE", {"tooltip": "要发送的图像"}),
                "filename_prefix": ("STRING", {"default": "lg_send"}),
                "link_id": ("INT", {"default": 1, "min": 0, "max": sys.maxsize, "step": 1, "tooltip": "发送端连接ID"}),
                "accumulate": ("BOOLEAN", {"default": False, "tooltip": "开启后将累积所有图像一起发送"}), 
                "preview_rgba": ("BOOLEAN", {"default": True, "tooltip": "开启后预览显示RGBA格式，关闭则预览显示RGB格式"})
            },
            "optional": {
                "masks": ("MASK", {"tooltip": "要发送的遮罩"}),
                "signal_opt": (any_typ, {"tooltip": "信号输入，将在处理完成后原样输出"})
            },
            "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"},
        }

    RETURN_TYPES = (any_typ,)
    RETURN_NAMES = ("signal",)
    FUNCTION = "save_images"
    CATEGORY = CATEGORY_TYPE
    INPUT_IS_LIST = True
    OUTPUT_IS_LIST = (True,)
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(s, images, filename_prefix, link_id, accumulate, preview_rgba, masks=None, prompt=None, extra_pnginfo=None):
        if isinstance(accumulate, list):
            accumulate = accumulate[0]
        
        if accumulate:
            return float("NaN") 
        
        # 非积累模式下计算hash
        hash_value = hash(str(images) + str(masks))
        return hash_value

    def save_images(self, images, filename_prefix, link_id, accumulate, preview_rgba, masks=None, prompt=None, extra_pnginfo=None):
        timestamp = int(time.time() * 1000)
        results = list()

        filename_prefix = filename_prefix[0] if isinstance(filename_prefix, list) else filename_prefix
        link_id = link_id[0] if isinstance(link_id, list) else link_id
        accumulate = accumulate[0] if isinstance(accumulate, list) else accumulate
        preview_rgba = preview_rgba[0] if isinstance(preview_rgba, list) else preview_rgba
        
        for idx, image_batch in enumerate(images):
            try:
                image = image_batch.squeeze()
                rgb_image = Image.fromarray(np.clip(255. * image.cpu().numpy(), 0, 255).astype(np.uint8))

                if masks is not None and idx < len(masks):
                    mask = masks[idx].squeeze()
                    mask_img = Image.fromarray(np.clip(255. * (1 - mask.cpu().numpy()), 0, 255).astype(np.uint8))
                else:
                    mask_img = Image.new('L', rgb_image.size, 255)

                r, g, b = rgb_image.convert('RGB').split()
                rgba_image = Image.merge('RGBA', (r, g, b, mask_img))

                # 保存RGBA格式，这是实际要发送的文件
                filename = f"{filename_prefix}_{link_id}_{timestamp}_{idx}.png"
                file_path = os.path.join(self.output_dir, filename)
                rgba_image.save(file_path, compress_level=self.compress_level)
                
                # 准备要发送的数据项
                original_result = {
                    "filename": filename,
                    "subfolder": "",
                    "type": self.type
                }
                
                # 如果是要显示RGB预览
                if not preview_rgba:
                    preview_filename = f"{filename_prefix}_{link_id}_{timestamp}_{idx}_preview.jpg"
                    preview_path = os.path.join(self.output_dir, preview_filename)
                    rgb_image.save(preview_path, format="JPEG", quality=95)
                    # 将预览图添加到UI显示结果中
                    results.append({
                        "filename": preview_filename,
                        "subfolder": "",
                        "type": self.type
                    })
                else:
                    # 显示RGBA
                    results.append(original_result)

                # 累积的始终是原始图像结果
                if accumulate:
                    self.accumulated_results.append(original_result)

            except Exception as e:
                print(f"[ImageSender] 处理图像 {idx+1} 时出错: {str(e)}")
                import traceback
                traceback.print_exc()
                continue

        # 获取实际要发送的结果
        if accumulate:
            send_results = self.accumulated_results
        else:
            # 创建一个包含原始文件名的列表用于发送
            send_results = []
            for idx in range(len(results)):
                original_filename = f"{filename_prefix}_{link_id}_{timestamp}_{idx}.png"
                send_results.append({
                    "filename": original_filename,
                    "subfolder": "",
                    "type": self.type
                })
        
        if send_results:
            print(f"[ImageSender] 发送 {len(send_results)} 张图像")
            PromptServer.instance.send_sync("img-send", {
                "link_id": link_id,
                "images": send_results
            })
        if not accumulate:
            self.accumulated_results = []
        
        return { "ui": { "images": results } }

class LG_ImageReceiver:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "image": ("STRING", {"default": "", "multiline": False, "tooltip": "多个文件名用逗号分隔"}),
                "link_id": ("INT", {"default": 1, "min": 0, "max": sys.maxsize, "step": 1, "tooltip": "发送端连接ID"}),
            }
        }


    RETURN_TYPES = ("IMAGE", "MASK")
    RETURN_NAMES = ("images", "masks")
    CATEGORY = CATEGORY_TYPE
    OUTPUT_IS_LIST = (True, True)
    FUNCTION = "load_image"

    def load_image(self, image, link_id):
        image_files = [x.strip() for x in image.split(',') if x.strip()]
        print(f"[ImageReceiver] 加载图像: {image_files}")
        
        output_images = []
        output_masks = []
        
        if not image_files:
            empty_image = torch.zeros((1, 64, 64, 3), dtype=torch.float32)
            empty_mask = torch.zeros((1, 64, 64), dtype=torch.float32)
            return ([empty_image], [empty_mask])
        
        try:
            temp_dir = folder_paths.get_temp_directory()
            
            for img_file in image_files:
                try:
                    img_path = os.path.join(temp_dir, img_file)
                    
                    if not os.path.exists(img_path):
                        print(f"[ImageReceiver] 文件不存在: {img_path}")
                        continue
                    
                    img = Image.open(img_path)
                    
                    if img.mode == 'RGBA':
                        r, g, b, a = img.split()
                        rgb_image = Image.merge('RGB', (r, g, b))
                        image = np.array(rgb_image).astype(np.float32) / 255.0
                        image = torch.from_numpy(image)[None,]
                        mask = np.array(a).astype(np.float32) / 255.0
                        mask = torch.from_numpy(mask)[None,]
                        mask = 1.0 - mask
                    else:
                        image = np.array(img.convert('RGB')).astype(np.float32) / 255.0
                        image = torch.from_numpy(image)[None,]
                        mask = torch.zeros((1, image.shape[1], image.shape[2]), dtype=torch.float32, device="cpu")
                    
                    output_images.append(image)
                    output_masks.append(mask)
                    
                except Exception as e:
                    print(f"[ImageReceiver] 处理文件 {img_file} 时出错: {str(e)}")
                    import traceback
                    traceback.print_exc()
                    continue
            
            return (output_images, output_masks)

        except Exception as e:
            print(f"[ImageReceiver] 处理图像时出错: {str(e)}")
            return ([], [])

# ==========================================
# 新增：视频发送/接收节点
# ==========================================
class LG_VideoSender:
    def __init__(self):
        self.output_dir = folder_paths.get_temp_directory()
        self.type = "temp"
        self.accumulated_results = []
        
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "frames": ("IMAGE", {"tooltip": "要发送的视频帧序列 (Shape: [Batch, H, W, 3])"}),
                "filename_prefix": ("STRING", {"default": "lg_video_send"}),
                "link_id": ("INT", {"default": 1, "min": 0, "max": sys.maxsize, "step": 1}),
                "fps": ("FLOAT", {"default": 30.0, "min": 1.0, "max": 120.0, "step": 0.1}),
                "accumulate": ("BOOLEAN", {"default": False}),
            },
            "optional": {
                # 新增：每帧遮罩，shape [Batch, H, W]，值域 0-1，0=未遮罩 1=遮罩
                "masks": ("MASK", {"tooltip": "视频帧遮罩序列，与frames帧数对应"}),
                "signal_opt": (any_typ,),
            },
            "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"},
        }

    RETURN_TYPES = (any_typ,)
    RETURN_NAMES = ("signal",)
    FUNCTION = "save_video"
    CATEGORY = CATEGORY_TYPE
    INPUT_IS_LIST = True
    OUTPUT_IS_LIST = (True,)
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(s, frames, filename_prefix, link_id, fps, accumulate,
                   masks=None, signal_opt=None, prompt=None, extra_pnginfo=None):
        if isinstance(accumulate, list): accumulate = accumulate[0]
        if accumulate: return float("NaN")
        return hash(str(frames) + str(masks))

    def save_video(self, frames, filename_prefix, link_id, fps, accumulate,
                   masks=None, signal_opt=None, prompt=None, extra_pnginfo=None):
        timestamp = int(time.time() * 1000)
        results = []

        filename_prefix = filename_prefix[0] if isinstance(filename_prefix, list) else filename_prefix
        link_id = link_id[0] if isinstance(link_id, list) else link_id
        fps = fps[0] if isinstance(fps, list) else fps
        accumulate = accumulate[0] if isinstance(accumulate, list) else accumulate

        for idx, frame_batch in enumerate(frames):
            try:
                frame_np = frame_batch.cpu().numpy()
                frame_np = np.clip(255. * frame_np, 0, 255).astype(np.uint8)
                if frame_np.ndim == 3:
                    frame_np = frame_np[np.newaxis, ...]

                num_frames, H, W, _ = frame_np.shape

                # ---- 保存 RGB 视频 ----
                filename = f"{filename_prefix}_{link_id}_{timestamp}_{idx}.mp4"
                file_path = os.path.join(self.output_dir, filename)
                fourcc = cv2.VideoWriter_fourcc(*'mp4v')
                out = cv2.VideoWriter(file_path, fourcc, fps, (W, H))
                for i in range(num_frames):
                    out.write(cv2.cvtColor(frame_np[i], cv2.COLOR_RGB2BGR))
                out.release()
                print(f"[VideoSender] 保存视频: {filename}, 帧数: {num_frames}")

                # ---- 新增：保存遮罩视频（灰度 mp4）----
                if masks is not None and idx < len(masks):
                    try:
                        mask_batch = masks[idx]
                        mask_np = mask_batch.cpu().numpy()  # [Batch, H, W] 或 [H, W]
                        if mask_np.ndim == 2:
                            mask_np = mask_np[np.newaxis, ...]
                        mask_np = np.clip(mask_np * 255, 0, 255).astype(np.uint8)

                        mask_filename = filename.replace('.mp4', '_mask.mp4')
                        mask_path = os.path.join(self.output_dir, mask_filename)
                        # isColor=False 写入灰度视频
                        mask_out = cv2.VideoWriter(
                            mask_path, fourcc, fps, (W, H), isColor=False
                        )
                        n_mask_frames = min(num_frames, mask_np.shape[0])
                        for i in range(n_mask_frames):
                            mask_out.write(mask_np[i])
                        # 如果mask帧数不够，补最后一帧
                        for _ in range(num_frames - n_mask_frames):
                            mask_out.write(mask_np[-1])
                        mask_out.release()
                        print(f"[VideoSender] 保存遮罩视频: {mask_filename}")
                    except Exception as e:
                        print(f"[VideoSender] 保存遮罩时出错: {str(e)}")

                video_result = {"filename": filename, "subfolder": "", "type": self.type}
                results.append(video_result)
                if accumulate:
                    self.accumulated_results.append(video_result)

            except Exception as e:
                print(f"[VideoSender] 处理视频 {idx+1} 时出错: {str(e)}")
                import traceback; traceback.print_exc()

        send_results = self.accumulated_results if accumulate else results
        if send_results:
            PromptServer.instance.send_sync("video-send", {
                "link_id": link_id, "videos": send_results
            })
        if not accumulate:
            self.accumulated_results = []

        return {"ui": {"videos": results}}

class LG_VideoReceiver:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "video": ("STRING", {"default": "", "multiline": False,
                          "tooltip": "多个视频文件名用逗号分隔"}),
                "link_id": ("INT", {"default": 1, "min": 0, "max": sys.maxsize, "step": 1}),
            }
        }

    # 新增 MASK 输出
    RETURN_TYPES = ("IMAGE", "MASK")
    RETURN_NAMES = ("frames", "masks")
    CATEGORY = CATEGORY_TYPE
    OUTPUT_IS_LIST = (True, True)
    FUNCTION = "load_video"

    def load_video(self, video, link_id):
        video_files = [x.strip() for x in video.split(',') if x.strip()]
        print(f"[VideoReceiver] 加载视频: {video_files}")

        output_frames = []
        output_masks = []
        temp_dir = folder_paths.get_temp_directory()

        def empty_frames():
            return torch.zeros((1, 64, 64, 3), dtype=torch.float32)

        def empty_mask(n, h, w):
            # 0 = 未遮罩，返回全黑遮罩（无遮罩效果）
            return torch.zeros((n, h, w), dtype=torch.float32)

        if not video_files:
            t = empty_frames()
            return ([t], [empty_mask(1, 64, 64)])

        for vid_file in video_files:
            try:
                vid_path = os.path.join(temp_dir, vid_file)
                if not os.path.exists(vid_path):
                    print(f"[VideoReceiver] 视频文件不存在: {vid_path}")
                    continue

                # ---- 加载 RGB 帧 ----
                cap = cv2.VideoCapture(vid_path)
                frames = []
                while True:
                    ret, frame = cap.read()
                    if not ret: break
                    frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                cap.release()

                if not frames:
                    continue

                frames_np = np.stack(frames, axis=0).astype(np.float32) / 255.0
                frames_tensor = torch.from_numpy(frames_np)  # [N, H, W, 3]
                output_frames.append(frames_tensor)
                N, H, W, _ = frames_tensor.shape

                # ---- 新增：加载遮罩视频 ----
                mask_file = vid_file.replace('.mp4', '_mask.mp4')
                mask_path = os.path.join(temp_dir, mask_file)

                if os.path.exists(mask_path):
                    cap_mask = cv2.VideoCapture(mask_path)
                    mask_frames = []
                    while True:
                        ret, frame = cap_mask.read()
                        if not ret: break
                        # 灰度视频读出来可能是 [H,W] 或 [H,W,1/3]
                        if frame.ndim == 3:
                            frame = frame[:, :, 0]
                        mask_frames.append(frame)
                    cap_mask.release()

                    if mask_frames:
                        mask_np = np.stack(mask_frames, axis=0).astype(np.float32) / 255.0
                        masks_tensor = torch.from_numpy(mask_np)  # [N, H, W]
                        # 补齐帧数（以防不一致）
                        if masks_tensor.shape[0] < N:
                            pad = masks_tensor[-1:].expand(N - masks_tensor.shape[0], H, W)
                            masks_tensor = torch.cat([masks_tensor, pad], dim=0)
                        output_masks.append(masks_tensor[:N])
                        print(f"[VideoReceiver] 已加载遮罩: {mask_file}")
                    else:
                        output_masks.append(empty_mask(N, H, W))
                else:
                    # 没有遮罩文件，返回空遮罩
                    output_masks.append(empty_mask(N, H, W))
                    print(f"[VideoReceiver] 无遮罩文件，使用空遮罩")

            except Exception as e:
                print(f"[VideoReceiver] 处理 {vid_file} 时出错: {str(e)}")
                import traceback; traceback.print_exc()

        if not output_frames:
            t = empty_frames()
            return ([t], [empty_mask(1, 64, 64)])

        return (output_frames, output_masks)

# ==========================================
# 新增：字符串发送/接收节点
# ==========================================
class LG_StringSender:
    def __init__(self):
        self.output_dir = folder_paths.get_temp_directory()
        self.type = "temp"
        self.accumulated_results = []
        
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "text": ("STRING", {"default": "", "multiline": True, "tooltip": "要发送的字符串内容"}),
                "filename_prefix": ("STRING", {"default": "lg_string_send"}),
                "link_id": ("INT", {"default": 1, "min": 0, "max": sys.maxsize, "step": 1, "tooltip": "发送端连接ID"}),
                "accumulate": ("BOOLEAN", {"default": False, "tooltip": "开启后将累积所有字符串一起发送"}),
            },
            "optional": {
                "signal_opt": (any_typ, {"tooltip": "信号输入，将在处理完成后原样输出"})
            },
            "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"},
        }

    RETURN_TYPES = (any_typ,)
    RETURN_NAMES = ("signal",)
    FUNCTION = "save_string"
    CATEGORY = CATEGORY_TYPE
    INPUT_IS_LIST = True
    OUTPUT_IS_LIST = (True,)
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(s, text, filename_prefix, link_id, accumulate, signal_opt=None, prompt=None, extra_pnginfo=None):
        if isinstance(accumulate, list): accumulate = accumulate[0]
        if accumulate: return float("NaN")
        return hash(str(text))

    def save_string(self, text, filename_prefix, link_id, accumulate, signal_opt=None, prompt=None, extra_pnginfo=None):
        timestamp = int(time.time() * 1000)
        results = []

        # 处理列表输入
        filename_prefix = filename_prefix[0] if isinstance(filename_prefix, list) else filename_prefix
        link_id = link_id[0] if isinstance(link_id, list) else link_id
        accumulate = accumulate[0] if isinstance(accumulate, list) else accumulate
        
        # 确保text是列表
        if not isinstance(text, list):
            text = [text]

        for idx, txt in enumerate(text):
            try:
                filename = f"{filename_prefix}_{link_id}_{timestamp}_{idx}.txt"
                file_path = os.path.join(self.output_dir, filename)
                
                # 写入文本文件
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(txt)
                
                text_result = {
                    "filename": filename,
                    "subfolder": "",
                    "type": self.type
                }
                results.append(text_result)

                if accumulate:
                    self.accumulated_results.append(text_result)

            except Exception as e:
                print(f"[StringSender] 处理字符串 {idx+1} 时出错: {str(e)}")
                import traceback
                traceback.print_exc()
                continue

        send_results = self.accumulated_results if accumulate else results
        
        if send_results:
            print(f"[StringSender] 发送 {len(send_results)} 个字符串文件")
            PromptServer.instance.send_sync("string-send", {
                "link_id": link_id,
                "strings": send_results
            })
        
        if not accumulate:
            self.accumulated_results = []
        
        # UI显示文本内容预览
        ui_results = [{"filename": r["filename"], "content": t} for r, t in zip(results, text)]
        return { "ui": { "strings": ui_results } }

class LG_StringReceiver:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "string": ("STRING", {"default": "", "multiline": False, "tooltip": "多个字符串文件名用逗号分隔"}),
                "link_id": ("INT", {"default": 1, "min": 0, "max": sys.maxsize, "step": 1, "tooltip": "发送端连接ID"}),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("text",)
    CATEGORY = CATEGORY_TYPE
    OUTPUT_IS_LIST = (True,)
    FUNCTION = "load_string"

    def load_string(self, string, link_id):
        string_files = [x.strip() for x in string.split(',') if x.strip()]
        print(f"[StringReceiver] 加载字符串: {string_files}")
        
        output_strings = []
        
        if not string_files:
            return ([""],)
        
        try:
            temp_dir = folder_paths.get_temp_directory()
            
            for str_file in string_files:
                try:
                    str_path = os.path.join(temp_dir, str_file)
                    
                    if not os.path.exists(str_path):
                        print(f"[StringReceiver] 文件不存在: {str_path}")
                        continue
                    
                    with open(str_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                    
                    output_strings.append(content)
                    
                except Exception as e:
                    print(f"[StringReceiver] 处理文件 {str_file} 时出错: {str(e)}")
                    import traceback
                    traceback.print_exc()
                    continue
            
            return (output_strings if output_strings else [""],)

        except Exception as e:
            print(f"[StringReceiver] 处理字符串时出错: {str(e)}")
            return ([""],)

# 请在文件顶部添加必要的导入
import torchaudio

# ==========================================
# 新增：音频发送/接收节点
# ==========================================
class LG_audioSender:
    def __init__(self):
        self.output_dir = folder_paths.get_temp_directory()
        self.type = "temp"
        self.accumulated_results = []
        
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "audio": ("AUDIO", {"tooltip": "要发送的音频"}),
                "filename_prefix": ("STRING", {"default": "lg_audio_send"}),
                "link_id": ("INT", {"default": 1, "min": 0, "max": sys.maxsize, "step": 1, "tooltip": "发送端连接ID"}),
                "accumulate": ("BOOLEAN", {"default": False, "tooltip": "开启后将累积所有音频一起发送"}),
            },
            "optional": {
                "signal_opt": (any_typ, {"tooltip": "信号输入，将在处理完成后原样输出"})
            },
            "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"},
        }

    RETURN_TYPES = (any_typ,)
    RETURN_NAMES = ("signal",)
    FUNCTION = "save_audio"
    CATEGORY = CATEGORY_TYPE
    INPUT_IS_LIST = True
    OUTPUT_IS_LIST = (True,)
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(s, audio, filename_prefix, link_id, accumulate, signal_opt=None, prompt=None, extra_pnginfo=None):
        if isinstance(accumulate, list):
            accumulate = accumulate[0]
        if accumulate:
            return float("NaN")
        return hash(str(audio))

    def save_audio(self, audio, filename_prefix, link_id, accumulate, signal_opt=None, prompt=None, extra_pnginfo=None):
        timestamp = int(time.time() * 1000)
        results = []

        # 处理列表输入
        filename_prefix = filename_prefix[0] if isinstance(filename_prefix, list) else filename_prefix
        link_id = link_id[0] if isinstance(link_id, list) else link_id
        accumulate = accumulate[0] if isinstance(accumulate, list) else accumulate

        for idx, audio_batch in enumerate(audio):
            try:
                # 提取音频数据 (移除batch维度)
                waveform = audio_batch["waveform"].squeeze(0)
                sample_rate = audio_batch["sample_rate"]

                # 保存为WAV文件
                filename = f"{filename_prefix}_{link_id}_{timestamp}_{idx}.wav"
                file_path = os.path.join(self.output_dir, filename)
                
                torchaudio.save(
                    file_path, 
                    waveform, 
                    sample_rate, 
                    format="wav", 
                    bits_per_sample=16, 
                    encoding="PCM_S"
                )

                audio_result = {
                    "filename": filename,
                    "subfolder": "",
                    "type": self.type
                }
                results.append(audio_result)

                if accumulate:
                    self.accumulated_results.append(audio_result)

            except Exception as e:
                print(f"[AudioSender] 处理音频 {idx+1} 时出错: {str(e)}")
                import traceback
                traceback.print_exc()
                continue

        send_results = self.accumulated_results if accumulate else results
        
        if send_results:
            print(f"[AudioSender] 发送 {len(send_results)} 个音频")
            PromptServer.instance.send_sync("audio-send", {
                "link_id": link_id,
                "audios": send_results
            })
        
        if not accumulate:
            self.accumulated_results = []
        
        return { "ui": { "audios": results } }

class LG_audioReceiver:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "audio": ("STRING", {"default": "", "multiline": False, "tooltip": "多个音频文件名用逗号分隔"}),
                "link_id": ("INT", {"default": 1, "min": 0, "max": sys.maxsize, "step": 1, "tooltip": "发送端连接ID"}),
            }
        }

    RETURN_TYPES = ("AUDIO",)
    RETURN_NAMES = ("audios",)
    CATEGORY = CATEGORY_TYPE
    OUTPUT_IS_LIST = (True,)
    FUNCTION = "load_audio"

    def load_audio(self, audio, link_id):
        audio_files = [x.strip() for x in audio.split(',') if x.strip()]
        print(f"[AudioReceiver] 加载音频: {audio_files}")
        
        output_audios = []
        
        # 默认空音频
        def get_empty_audio():
            empty_waveform = torch.zeros((1, 1, 44100), dtype=torch.float32)
            return {"waveform": empty_waveform, "sample_rate": 44100}
        
        if not audio_files:
            return ([get_empty_audio()],)
        
        try:
            temp_dir = folder_paths.get_temp_directory()
            
            for aud_file in audio_files:
                try:
                    aud_path = os.path.join(temp_dir, aud_file)
                    
                    if not os.path.exists(aud_path):
                        print(f"[AudioReceiver] 文件不存在: {aud_path}")
                        continue
                    
                    # 加载音频并添加batch维度
                    waveform, sample_rate = torchaudio.load(aud_path)
                    waveform = waveform.unsqueeze(0)
                    
                    output_audio = {
                        "waveform": waveform,
                        "sample_rate": sample_rate
                    }
                    output_audios.append(output_audio)
                    
                except Exception as e:
                    print(f"[AudioReceiver] 处理文件 {aud_file} 时出错: {str(e)}")
                    import traceback
                    traceback.print_exc()
                    continue
            
            return (output_audios if output_audios else [get_empty_audio()],)

        except Exception as e:
            print(f"[AudioReceiver] 处理音频时出错: {str(e)}")
            return ([get_empty_audio()],)

# ==========================================
# 新增：Latent 发送/接收节点 (对齐现有代码逻辑)
# ==========================================
import comfy.utils
from comfy.cli_args import args
import safetensors.torch  # 新增：导入 safetensors

class LG_LatentSender:
    def __init__(self):
        self.output_dir = folder_paths.get_temp_directory()
        self.type = "temp"
        self.accumulated_results = []
        
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "latents": ("LATENT", {"tooltip": "要发送的Latent数据 (Shape: [samples, height, width])"}),
                "filename_prefix": ("STRING", {"default": "lg_latent_send"}),
                "link_id": ("INT", {"default": 1, "min": 0, "max": sys.maxsize, "step": 1, "tooltip": "发送端连接ID"}),
                "accumulate": ("BOOLEAN", {"default": False, "tooltip": "开启后将累积所有Latent一起发送"}),
            },
            "optional": {
                "signal_opt": (any_typ, {"tooltip": "信号输入，将在处理完成后原样输出"})
            },
            "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"},
        }

    RETURN_TYPES = (any_typ,)
    RETURN_NAMES = ("signal",)
    FUNCTION = "save_latent"
    CATEGORY = CATEGORY_TYPE
    INPUT_IS_LIST = True
    OUTPUT_IS_LIST = (True,)
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(s, latents, filename_prefix, link_id, accumulate, signal_opt=None, prompt=None, extra_pnginfo=None):
        if isinstance(accumulate, list): accumulate = accumulate[0]
        if accumulate: return float("NaN")
        return hash(str(latents))

    def save_latent(self, latents, filename_prefix, link_id, accumulate, signal_opt=None, prompt=None, extra_pnginfo=None):
        timestamp = int(time.time() * 1000)
        results = []

        # 处理列表输入
        filename_prefix = filename_prefix[0] if isinstance(filename_prefix, list) else filename_prefix
        link_id = link_id[0] if isinstance(link_id, list) else link_id
        accumulate = accumulate[0] if isinstance(accumulate, list) else accumulate
        prompt = prompt[0] if isinstance(prompt, list) else prompt
        extra_pnginfo = extra_pnginfo[0] if isinstance(extra_pnginfo, list) else extra_pnginfo

        for idx, latent_batch in enumerate(latents):
            try:
                if not isinstance(latent_batch, dict) or "samples" not in latent_batch:
                    print(f"[LatentSender] 无效的Latent数据，跳过索引 {idx}")
                    continue
                
                # 提取核心张量
                latent_samples = latent_batch["samples"]

                # 保存为.latent文件（对齐官方 SaveLatent + safetensors 格式）
                filename = f"{filename_prefix}_{link_id}_{timestamp}_{idx}.latent"
                file_path = os.path.join(self.output_dir, filename)
                
                # ---------------- 修正部分开始 ----------------
                # 1. 构建保存数据（包含版本标记，避免后续需要乘缩放因子）
                save_dict = {
                    "latent_tensor": latent_samples.contiguous(),
                    "latent_format_version_0": torch.tensor([])
                }
                
                # 2. 使用 safetensors.torch.save_file 保存（官方格式）
                safetensors.torch.save_file(save_dict, file_path)
                # ---------------- 修正部分结束 ----------------

                # 构建结果对象
                latent_result = {
                    "filename": filename,
                    "subfolder": "",
                    "type": self.type
                }
                results.append(latent_result)

                if accumulate:
                    self.accumulated_results.append(latent_result)

            except Exception as e:
                print(f"[LatentSender] 处理Latent {idx+1} 时出错: {str(e)}")
                import traceback
                traceback.print_exc()
                continue

        send_results = self.accumulated_results if accumulate else results
        
        if send_results:
            print(f"[LatentSender] 发送 {len(send_results)} 个Latent文件")
            PromptServer.instance.send_sync("latent-send", {
                "link_id": link_id,
                "latents": send_results
            })
        
        if not accumulate:
            self.accumulated_results = []
        
        return { "ui": { "latents": results } }

import safetensors.torch  # 新增：导入 safetensors

class LG_LatentReceiver:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "latent": ("STRING", {"default": "", "multiline": False, "tooltip": "多个Latent文件名用逗号分隔"}),
                "link_id": ("INT", {"default": 1, "min": 0, "max": sys.maxsize, "step": 1, "tooltip": "发送端连接ID"}),
                "merge_latent": ("BOOLEAN", {"default": False, "tooltip": "如果为True，将多个Latent融合为一个（按batch拼接）"}),
            }
        }

    RETURN_TYPES = ("LATENT",)
    RETURN_NAMES = ("latents",)
    CATEGORY = CATEGORY_TYPE
    OUTPUT_IS_LIST = (True,)
    FUNCTION = "load_latent"

    def load_latent(self, latent, link_id, merge_latent=False):
        latent_files = [x.strip() for x in latent.split(',') if x.strip()]
        print(f"[LatentReceiver] 加载Latent: {latent_files}")

        output_latents = []

        # 空数据兜底
        def get_empty_latent():
            empty_samples = torch.zeros((1, 4, 64, 64), dtype=torch.float32)
            return {"samples": empty_samples}

        if not latent_files:
            return ([get_empty_latent()],)

        try:
            temp_dir = folder_paths.get_temp_directory()

            for lat_file in latent_files:
                try:
                    lat_path = os.path.join(temp_dir, lat_file)

                    if not os.path.exists(lat_path):
                        print(f"[LatentReceiver] 文件不存在: {lat_path}")
                        continue

                    # 使用 safetensors.torch.load_file 加载
                    latent_data = safetensors.torch.load_file(lat_path, device="cpu")

                    # 处理缩放因子 multiplier
                    multiplier = 1.0
                    if "latent_format_version_0" not in latent_data:
                        multiplier = 1.0 / 0.18215

                    # 构建标准 Latent 结构（只需要 "samples" 键）
                    samples = {
                        "samples": latent_data["latent_tensor"].float() * multiplier
                    }

                    output_latents.append(samples)

                except Exception as e:
                    print(f"[LatentReceiver] 处理文件 {lat_file} 时出错: {str(e)}")
                    import traceback
                    traceback.print_exc()
                    continue

            # 新增融合逻辑
            if merge_latent and output_latents:
                # 提取所有 samples 并沿 batch 维度拼接
                samples_list = [lat["samples"] for lat in output_latents]
                merged_samples = torch.cat(samples_list, dim=0)
                merged_latent = {"samples": merged_samples}
                return ([merged_latent],)
            else:
                # 原有逻辑：返回列表（可能为空时已处理）
                return (output_latents if output_latents else [get_empty_latent()],)

        except Exception as e:
            print(f"[LatentReceiver] 处理Latent时出错: {str(e)}")
            return ([get_empty_latent()],)

class ImageListSplitter:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "indices": ("STRING", {
                    "default": "", 
                    "multiline": False,
                    "tooltip": "输入要提取的图片索引，用逗号分隔，如：0,1,3,4"
                }),
            },
        }
    
    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("images",)
    FUNCTION = "split_images"
    CATEGORY = CATEGORY_TYPE

    INPUT_IS_LIST = True
    OUTPUT_IS_LIST = (True,)  # (images,)

    def split_images(self, images, indices):
        try:
            # 解析索引字符串
            try:
                if isinstance(indices, list):
                    indices = indices[0] if indices else ""
                indices = [int(idx.strip()) for idx in indices.split(',') if idx.strip()]
            except ValueError:
                print("[ImageSplitter] 索引格式错误，请使用逗号分隔的数字")
                return ([],)
            
            # 确保images是列表
            if not isinstance(images, list):
                images = [images]
            
            # 处理批量图片的情况
            if len(images) == 1 and len(images[0].shape) == 4:  # [B, H, W, C]
                batch_images = images[0]
                total_images = batch_images.shape[0]
                print(f"[ImageSplitter] 检测到批量图片，总数: {total_images}")
                
                selected_images = []
                for idx in indices:
                    if 0 <= idx < total_images:
                        # 保持批次维度，使用unsqueeze确保维度为 [1, H, W, C]
                        img = batch_images[idx].unsqueeze(0)
                        selected_images.append(img)
                        print(f"[ImageSplitter] 从批量中选择第 {idx} 张图片")
                    else:
                        print(f"[ImageSplitter] 索引 {idx} 超出批量范围 0-{total_images-1}")
                
                if not selected_images:
                    return ([],)
                return (selected_images,)
            
            # 处理图片列表的情况
            total_images = len(images)
            print(f"[ImageSplitter] 检测到图片列表，总数: {total_images}")
            
            if total_images == 0:
                print("[ImageSplitter] 没有输入图片")
                return ([],)
            
            selected_images = []
            for idx in indices:
                if 0 <= idx < total_images:
                    selected_image = images[idx]
                    # 确保输出维度为 [1, H, W, C]
                    if len(selected_image.shape) == 3:  # [H, W, C]
                        selected_image = selected_image.unsqueeze(0)
                    selected_images.append(selected_image)
                    print(f"[ImageSplitter] 从列表中选择第 {idx} 张图片")
                else:
                    print(f"[ImageSplitter] 索引 {idx} 超出列表范围 0-{total_images-1}")
            
            if not selected_images:
                return ([],)
            return (selected_images,)

        except Exception as e:
            print(f"[ImageSplitter] 处理出错: {str(e)}")
            return ([],)

class MaskListSplitter:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "masks": ("MASK",),
                "indices": ("STRING", {
                    "default": "", 
                    "multiline": False,
                    "tooltip": "输入要提取的遮罩索引，用逗号分隔，如：0,1,3,4"
                }),
            },
        }
    
    RETURN_TYPES = ("MASK",)
    RETURN_NAMES = ("masks",)
    FUNCTION = "split_masks"
    CATEGORY = CATEGORY_TYPE

    INPUT_IS_LIST = True
    OUTPUT_IS_LIST = (True,)  # (masks,)

    def split_masks(self, masks, indices):
        try:
            # 解析索引字符串
            try:
                if isinstance(indices, list):
                    indices = indices[0] if indices else ""
                indices = [int(idx.strip()) for idx in indices.split(',') if idx.strip()]
            except ValueError:
                print("[MaskSplitter] 索引格式错误，请使用逗号分隔的数字")
                return ([],)
            
            # 确保masks是列表
            if not isinstance(masks, list):
                masks = [masks]
            
            # 处理批量遮罩的情况
            if len(masks) == 1 and len(masks[0].shape) == 3:  # [B, H, W]
                batch_masks = masks[0]
                total_masks = batch_masks.shape[0]
                print(f"[MaskSplitter] 检测到批量遮罩，总数: {total_masks}")
                
                selected_masks = []
                for idx in indices:
                    if 0 <= idx < total_masks:
                        selected_masks.append(batch_masks[idx].unsqueeze(0))
                        print(f"[MaskSplitter] 从批量中选择第 {idx} 个遮罩")
                    else:
                        print(f"[MaskSplitter] 索引 {idx} 超出批量范围 0-{total_masks-1}")
                
                if not selected_masks:
                    return ([],)
                return (selected_masks,)
            
            # 处理遮罩列表的情况
            total_masks = len(masks)
            print(f"[MaskSplitter] 检测到遮罩列表，总数: {total_masks}")
            
            if total_masks == 0:
                print("[MaskSplitter] 没有输入遮罩")
                return ([],)
            
            selected_masks = []
            for idx in indices:
                if 0 <= idx < total_masks:
                    selected_mask = masks[idx]
                    if len(selected_mask.shape) == 2:  # [H, W]
                        selected_mask = selected_mask.unsqueeze(0)
                    elif len(selected_mask.shape) != 3:  # 不是 [B, H, W]
                        print(f"[MaskSplitter] 不支持的遮罩维度: {selected_mask.shape}")
                        continue
                    selected_masks.append(selected_mask)
                    print(f"[MaskSplitter] 从列表中选择第 {idx} 个遮罩")
                else:
                    print(f"[MaskSplitter] 索引 {idx} 超出列表范围 0-{total_masks-1}")
            
            if not selected_masks:
                return ([],)
            return (selected_masks,)

        except Exception as e:
            print(f"[MaskSplitter] 处理出错: {str(e)}")
            return ([],)

class ImageListRepeater:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "repeat_times": ("INT", {
                    "default": 1,
                    "min": 1,
                    "max": 100,
                    "step": 1,
                    "tooltip": "每张图片重复的次数"
                }),
            },
        }
    
    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("images",)
    FUNCTION = "repeat_images"
    CATEGORY = CATEGORY_TYPE

    INPUT_IS_LIST = True
    OUTPUT_IS_LIST = (True,)

    def repeat_images(self, images, repeat_times):
        try:
            # 处理 repeat_times 参数
            if isinstance(repeat_times, list):
                repeat_times = repeat_times[0] if repeat_times else 1
            
            # 确保images是列表
            if not isinstance(images, list):
                images = [images]
            
            if len(images) == 0:
                print("[ImageRepeater] 没有输入图片")
                return ([],)
            
            # 创建重复后的图片列表
            repeated_images = []
            for idx, img in enumerate(images):
                for _ in range(int(repeat_times)):  # 确保 repeat_times 是整数
                    repeated_images.append(img)
                print(f"[ImageRepeater] 图片 {idx} 重复 {repeat_times} 次")
            
            print(f"[ImageRepeater] 输入 {len(images)} 张图片，输出 {len(repeated_images)} 张图片")
            return (repeated_images,)

        except Exception as e:
            print(f"[ImageRepeater] 处理出错: {str(e)}")
            return ([],)

class MaskListRepeater:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "masks": ("MASK",),
                "repeat_times": ("INT", {
                    "default": 1,
                    "min": 1,
                    "max": 100,
                    "step": 1,
                    "tooltip": "每张遮罩重复的次数"
                }),
            },
        }
    
    RETURN_TYPES = ("MASK",)            
    RETURN_NAMES = ("masks",)
    FUNCTION = "repeat_masks"
    CATEGORY = CATEGORY_TYPE

    INPUT_IS_LIST = True
    OUTPUT_IS_LIST = (True,)    

    def repeat_masks(self, masks, repeat_times):
        try:
            # 处理 repeat_times 参数
            if isinstance(repeat_times, list):
                repeat_times = repeat_times[0] if repeat_times else 1

            # 确保masks是列表
            if not isinstance(masks, list):
                masks = [masks]

            if len(masks) == 0:
                print("[MaskRepeater] 没有输入遮罩")
                return ([],)

            # 创建重复后的遮罩列表
            repeated_masks = []     
            for idx, mask in enumerate(masks):
                for _ in range(int(repeat_times)):  # 确保 repeat_times 是整数
                    repeated_masks.append(mask)
                print(f"[MaskRepeater] 遮罩 {idx} 重复 {repeat_times} 次")

            print(f"[MaskRepeater] 输入 {len(masks)} 个遮罩，输出 {len(repeated_masks)} 个遮罩")
            return (repeated_masks,)    

        except Exception as e:
            print(f"[MaskRepeater] 处理出错: {str(e)}")
            return ([],)


    
class LG_FastPreview(SaveImage):
    def __init__(self):
        self.output_dir = folder_paths.get_temp_directory()
        self.type = "temp"
        self.prefix_append = "_temp_" + ''.join(random.choice("abcdefghijklmnopqrstupvxyz") for x in range(5))
        
    @classmethod
    def INPUT_TYPES(s):
        return {"required": {
                    "images": ("IMAGE", ),
                    "format": (["PNG", "JPEG", "WEBP"], {"default": "JPEG"}),
                    "quality": ("INT", {"default": 95, "min": 1, "max": 100, "step": 1}),
                },
                "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"},
               }
    
    RETURN_TYPES = ()
    FUNCTION = "save_images"
    
    CATEGORY = CATEGORY_TYPE
    DESCRIPTION = "快速预览图像,支持多种格式和质量设置"

    def save_images(self, images, format="JPEG", quality=95, prompt=None, extra_pnginfo=None):
        filename_prefix = "preview"
        filename_prefix += self.prefix_append
        full_output_folder, filename, counter, subfolder, filename_prefix = folder_paths.get_save_image_path(filename_prefix, self.output_dir, images[0].shape[1], images[0].shape[0])
        
        results = list()
        for (batch_number, image) in enumerate(images):
            i = 255. * image.cpu().numpy()
            img = Image.fromarray(np.clip(i, 0, 255).astype(np.uint8))
            save_kwargs = {}
            if format == "PNG":
                file_extension = ".png"

                compress_level = int(9 * (1 - quality/100)) 
                save_kwargs["compress_level"] = compress_level

                if not args.disable_metadata:
                    metadata = PngInfo()
                    if prompt is not None:
                        metadata.add_text("prompt", json.dumps(prompt))
                    if extra_pnginfo is not None:
                        for x in extra_pnginfo:
                            metadata.add_text(x, json.dumps(extra_pnginfo[x]))
                    save_kwargs["pnginfo"] = metadata
            elif format == "JPEG":
                file_extension = ".jpg"
                save_kwargs["quality"] = quality
                save_kwargs["optimize"] = True
            else:  
                file_extension = ".webp"
                save_kwargs["quality"] = quality
                
            filename_with_batch_num = filename.replace("%batch_num%", str(batch_number))
            file = f"{filename_with_batch_num}_{counter:05}_{file_extension}"
            
            img.save(os.path.join(full_output_folder, file), format=format, **save_kwargs)
            
            results.append({
                "filename": file,
                "subfolder": subfolder,
                "type": self.type
            })
            counter += 1

        return { "ui": { "images": results } }
    
class LG_AccumulatePreview(SaveImage):
    def __init__(self):
        self.output_dir = folder_paths.get_temp_directory()
        self.type = "temp"
        self.prefix_append = "_acc_" + ''.join(random.choice("abcdefghijklmnopqrstupvxyz") for x in range(5))
        self.accumulated_images = []
        self.accumulated_masks = []
        self.counter = 0
        
    @classmethod
    def INPUT_TYPES(s):
        return {"required": {
                    "images": ("IMAGE", ),
                },
                "optional": {
                    "mask": ("MASK",),
                },
                "hidden": {
                    "prompt": "PROMPT", 
                    "extra_pnginfo": "EXTRA_PNGINFO",
                    "unique_id": "UNIQUE_ID"
                },
               }
    
    RETURN_TYPES = ("IMAGE", "MASK", "INT")
    RETURN_NAMES = ("images", "masks", "image_count")
    FUNCTION = "accumulate_images"
    OUTPUT_NODE = True
    OUTPUT_IS_LIST = (True, True, False)
    CATEGORY = CATEGORY_TYPE
    DESCRIPTION = "累计图像预览"

    def accumulate_images(self, images, mask=None, prompt=None, extra_pnginfo=None, unique_id=None):
        # 添加调试信息
        print(f"[AccumulatePreview] accumulate_images - 当前累积图片数量: {len(self.accumulated_images)}")
        print(f"[AccumulatePreview] accumulate_images - 新输入图片数量: {len(images)}")
        print(f"[AccumulatePreview] accumulate_images - unique_id: {unique_id}")
        
        filename_prefix = "accumulate"
        filename_prefix += self.prefix_append

        full_output_folder, filename, _, subfolder, filename_prefix = folder_paths.get_save_image_path(
            filename_prefix, self.output_dir, images[0].shape[1], images[0].shape[0]
        )

        for image in images:
            i = 255. * image.cpu().numpy()
            img = Image.fromarray(np.clip(i, 0, 255).astype(np.uint8))

            file = f"{filename}_{self.counter:05}.png"
            img.save(os.path.join(full_output_folder, file), format="PNG")

            if len(image.shape) == 3:
                image = image.unsqueeze(0) 
            self.accumulated_images.append({
                "image": image,
                "info": {
                    "filename": file,
                    "subfolder": subfolder,
                    "type": self.type
                }
            })

            if mask is not None:
                if len(mask.shape) == 2:
                    mask = mask.unsqueeze(0)
                self.accumulated_masks.append(mask)
            else:
                self.accumulated_masks.append(None)
            
            self.counter += 1

        if not self.accumulated_images:
            return {"ui": {"images": []}, "result": ([], [], 0)}

        accumulated_tensors = []
        for item in self.accumulated_images:
            img = item["image"]
            if len(img.shape) == 3:  # [H, W, C]
                img = img.unsqueeze(0)  # 变成 [1, H, W, C]
            accumulated_tensors.append(img)

        accumulated_masks = [m for m in self.accumulated_masks if m is not None]
        
        ui_images = [item["info"] for item in self.accumulated_images]
        
        return {
            "ui": {"images": ui_images},
            "result": (accumulated_tensors, accumulated_masks, len(self.accumulated_images))
        }

class LG_ValueSender:
    """
    发送任意类型的值
    """
    
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "value": (any_typ,),
                "link_id": ("INT", {"default": 0, "min": 0, "max": sys.maxsize, "step": 1}),
            },
            "optional": {
                "signal_opt": (any_typ,),
            }
        }

    OUTPUT_NODE = True
    FUNCTION = "doit"
    CATEGORY = CATEGORY_TYPE
    RETURN_TYPES = (any_typ,)
    RETURN_NAMES = ("signal",)

    def doit(self, value, link_id=0, signal_opt=None):
        # 转换值为可序列化的字符串
        if value is None:
            send_value = ""
        elif isinstance(value, (str, int, float, bool)):
            send_value = str(value)
        elif hasattr(value, 'tolist'):
            # tensor/numpy array
            send_value = str(value.tolist())
        elif isinstance(value, (list, tuple)):
            send_value = str(list(value))
        elif isinstance(value, dict):
            send_value = str(value)
        else:
            send_value = str(value)
            
        print(f"[ValueSender] link_id={link_id}, 发送值: {send_value}")
        PromptServer.instance.send_sync("value-send-accumulate", {
            "link_id": link_id, 
            "value": send_value
        })
        
        return (signal_opt,)


class LG_ValueReceiver:
    """
    接收值，支持累积模式
    累积多次收到的值成列表
    """
    
    _accumulated_values = {}  # 类级别存储，按 link_id 分组
    
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "typ": (["STRING", "INT", "FLOAT", "BOOLEAN", "ANY"], {"default": "STRING"}),
                "value": ("STRING", {"default": "", "multiline": True, 
                    "tooltip": "接收到的值，由前端自动填充"}),
                "link_id": ("INT", {"default": 0, "min": 0, "max": sys.maxsize, "step": 1}),
                "accumulate": ("BOOLEAN", {"default": True, "tooltip": "开启后累积所有收到的值"}),
            },
        }

    FUNCTION = "doit"
    CATEGORY = CATEGORY_TYPE
    RETURN_TYPES = (any_typ, "INT")
    RETURN_NAMES = ("values", "count")
    OUTPUT_IS_LIST = (True, False)

    @classmethod
    def IS_CHANGED(cls, typ, value, link_id, accumulate):
        if accumulate:
            return float("NaN")  # 累积模式下总是执行
        return hash(str(value))

    def doit(self, typ, value, link_id=0, accumulate=True):
        # 解析当前收到的值
        current_values = [v.strip() for v in value.strip().split('\n') if v.strip()]
        
        if accumulate:
            # 累积模式：添加到累积列表
            if link_id not in LG_ValueReceiver._accumulated_values:
                LG_ValueReceiver._accumulated_values[link_id] = []
            
            for v in current_values:
                if v not in LG_ValueReceiver._accumulated_values[link_id]:
                    LG_ValueReceiver._accumulated_values[link_id].append(v)
            
            value_list = LG_ValueReceiver._accumulated_values[link_id].copy()
        else:
            # 非累积模式：只使用当前值，清空累积
            LG_ValueReceiver._accumulated_values[link_id] = []
            value_list = current_values
        
        if not value_list:
            return ([], 0)
        
        # 类型转换
        result = []
        for v in value_list:
            try:
                if typ == "INT":
                    result.append(int(v))
                elif typ == "FLOAT":
                    result.append(float(v))
                elif typ == "BOOLEAN":
                    result.append(v.lower() in ("true", "1", "yes"))
                else:
                    result.append(v)
            except (ValueError, TypeError):
                result.append(v)
        
        print(f"[ValueReceiver] link_id={link_id}, 输出 {len(result)} 个值")
        return (result, len(result))
    
    @classmethod
    def clear_accumulated(cls, link_id=None):
        """清空累积的值"""
        if link_id is None:
            cls._accumulated_values.clear()
        elif link_id in cls._accumulated_values:
            cls._accumulated_values[link_id] = []


class LG_ClearAccumulatedValues:
    """
    清空累积的值
    """
    
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "link_id": ("INT", {"default": -1, "min": -1, "max": sys.maxsize, "step": 1,
                    "tooltip": "-1 表示清空所有 link_id 的累积值"}),
            },
            "optional": {
                "signal_opt": (any_typ,),
            }
        }

    OUTPUT_NODE = True
    FUNCTION = "doit"
    CATEGORY = CATEGORY_TYPE
    RETURN_TYPES = (any_typ,)
    RETURN_NAMES = ("signal",)

    def doit(self, link_id=-1, signal_opt=None):
        if link_id < 0:
            LG_ValueReceiver.clear_accumulated()
            # 通知前端清空所有
            PromptServer.instance.send_sync("value-clear-accumulate", {"link_id": -1})
            print("[ClearAccumulatedValues] 清空所有累积值")
        else:
            LG_ValueReceiver.clear_accumulated(link_id)
            # 通知前端清空指定 link_id
            PromptServer.instance.send_sync("value-clear-accumulate", {"link_id": link_id})
            print(f"[ClearAccumulatedValues] 清空 link_id={link_id} 的累积值")
        
        return (signal_opt,)

