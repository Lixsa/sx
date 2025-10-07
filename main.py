from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Optional
import uvicorn
import os
import shutil
from datetime import datetime, timedelta
import sqlite3
from pathlib import Path
import uuid
import json
import qrcode
import io
import base64

app = FastAPI(title="健康建议API", description="执业医师健康建议管理系统")

# 配置CORS - 支持跨域请求
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://16.171.135.255",  # 生产环境前端域名
        "http://localhost:3000",  # 本地开发环境
        "http://127.0.0.1:3000",  # 本地开发环境
        "http://localhost:8080",  # 备用本地端口
        "http://127.0.0.1:8080",  # 备用本地端口
        "*"  # 允许所有域名（开发阶段）
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# 增加请求体大小限制到50MB
@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    # 设置最大请求体大小为50MB
    if request.method == "POST":
        request.scope["http_version"] = "1.1"
    response = await call_next(request)
    return response

# 生产环境路径配置 - 使用相对路径，避免权限问题
BASE_DIR = Path.cwd()
UPLOAD_DIR = BASE_DIR / "uploads"
STATIC_DIR = BASE_DIR / "static"
DB_FILE = BASE_DIR / "health_suggestions.db"
QR_CODES_DIR = BASE_DIR / "qr_codes"  # 新增二维码存储目录

# 确保目录存在
UPLOAD_DIR.mkdir(exist_ok=True, parents=True)
STATIC_DIR.mkdir(exist_ok=True, parents=True)
QR_CODES_DIR.mkdir(exist_ok=True, parents=True)  # 创建二维码目录

# 挂载静态文件目录
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/qr_codes", StaticFiles(directory=str(QR_CODES_DIR)), name="qr_codes")  # 挂载二维码目录

# 配置模板
templates = Jinja2Templates(directory=str(STATIC_DIR))

# 二维码会话存储（生产环境建议使用Redis）
qr_sessions = {}

# 数据模型
class HealthSuggestionBase(BaseModel):
    title: str
    content: str
    author: str
    tag: Optional[str] = None

class HealthSuggestionCreate(HealthSuggestionBase):
    pass

class HealthSuggestion(HealthSuggestionBase):
    id: int
    image_url: Optional[str] = None
    publish_time: str
    user_id: Optional[str] = None
    user_ip: Optional[str] = None
    
    class Config:
        from_attributes = True

class QRLoginRequest(BaseModel):
    session_id: str
    user_id: str
    user_name: str
    user_token: str

class QRLoginResponse(BaseModel):
    session_id: str
    qr_code_data: str
    qr_code_image_url: str  # 新增二维码图片URL字段
    expires_at: str

# 数据库初始化
def init_database():
    """初始化数据库"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # 创建健康建议表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS health_suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            author TEXT NOT NULL,
            tag TEXT,
            image_url TEXT,
            publish_time TEXT NOT NULL,
            user_id TEXT,
            user_ip TEXT
        )
    ''')
    
    # 创建用户会话表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_sessions (
            session_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            user_name TEXT NOT NULL,
            user_token TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            is_active INTEGER DEFAULT 1
        )
    ''')
    
    conn.commit()
    conn.close()
    print("数据库初始化完成")

def get_db_connection():
    """获取数据库连接"""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row  # 使结果可以通过列名访问
    return conn

# 启动时初始化数据库
init_database()

@app.get("/")
async def root():
    """根路径 - 返回主页面"""
    return FileResponse(str(STATIC_DIR / "index.html"))

@app.get("/api")
async def api_root():
    """API根路径"""
    return {"message": "健康建议API服务正在运行", "status": "production"}

@app.get("/api/test")
async def test_api():
    """测试API连接"""
    return {"message": "API连接正常", "timestamp": datetime.now().isoformat()}

# 二维码登录相关接口
@app.post("/api/qr-login/generate", response_model=QRLoginResponse)
async def generate_qr_code():
    """生成二维码会话"""
    session_id = str(uuid.uuid4())
    expires_at = datetime.now() + timedelta(minutes=5)  # 5分钟过期
    
    # 存储会话信息
    qr_sessions[session_id] = {
        "created_at": datetime.now(),
        "expires_at": expires_at,
        "is_bound": False,
        "user_info": None
    }
    
    # 生成二维码数据（包含可访问的URL）
    qr_code_data = f"http://16.171.135.255/confirm-login?loginId={session_id}"
    
    # 使用qrcode库生成二维码图片
    try:
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(qr_code_data)
        qr.make(fit=True)
        
        # 创建二维码图片
        qr_image = qr.make_image(fill_color="black", back_color="white")
        
        # 保存二维码图片到文件
        qr_filename = f"qr_code_{session_id}.png"
        qr_file_path = QR_CODES_DIR / qr_filename
        qr_image.save(qr_file_path)
        
        # 生成二维码图片的URL
        qr_code_image_url = f"/qr_codes/{qr_filename}"
        
        print(f"二维码生成成功: {qr_file_path}")
        
    except Exception as e:
        print(f"二维码生成失败: {e}")
        # 如果二维码生成失败，返回默认值
        qr_code_image_url = ""
    
    return QRLoginResponse(
        session_id=session_id,
        qr_code_data=qr_code_data,
        qr_code_image_url=qr_code_image_url,
        expires_at=expires_at.isoformat()
    )

@app.get("/confirm-login")
async def confirm_login_page(loginId: str):
    """确认登录页面（手机扫码后访问）"""
    if loginId not in qr_sessions:
        return {"error": "登录ID不存在或已过期", "status": "error"}
    
    session = qr_sessions[loginId]
    
    # 检查是否过期
    if datetime.now() > session["expires_at"]:
        del qr_sessions[loginId]
        return {"error": "登录ID已过期", "status": "expired"}
    
    # 标记为已确认
    session["is_bound"] = True
    session["user_info"] = {
        "user_id": f"user_{loginId[:8]}",
        "user_name": f"医生_{loginId[:8]}",
        "user_token": f"token_{loginId}"
    }
    
    print(f"登录确认成功: {loginId}")
    
    # 返回简单的确认页面
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>登录确认</title>
        <style>
            body {{ font-family: Arial, sans-serif; text-align: center; padding: 50px; }}
            .success {{ color: #28a745; font-size: 24px; }}
            .info {{ color: #666; margin-top: 20px; }}
        </style>
    </head>
    <body>
        <div class="success">✅ 登录确认成功！</div>
        <div class="info">请返回电脑端查看登录状态</div>
        <div class="info">登录ID: {loginId}</div>
    </body>
    </html>
    """

@app.get("/api/qr-login/check/{uuid}")
async def check_qr_login(uuid: str):
    """检查二维码登录状态 - 前端轮询接口"""
    if uuid not in qr_sessions:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")
    
    session = qr_sessions[uuid]
    
    # 检查是否过期
    if datetime.now() > session["expires_at"]:
        del qr_sessions[uuid]
        raise HTTPException(status_code=410, detail="会话已过期")
    
    # 检查是否已确认扫码
    if session.get("is_confirmed", False):
        return {
            "status": "success",
            "message": "登录成功",
            "uuid": uuid,
            "doctor_id": session.get("user_info", {}).get("doctor_id", ""),
            "confirmed_at": session.get("confirmed_at", datetime.now()).isoformat()
        }
    else:
        return {
            "status": "waiting",
            "message": "等待扫码确认",
            "uuid": uuid
        }

@app.post("/api/qr-login/bind")
async def bind_qr_login(request: QRLoginRequest):
    """绑定用户到二维码会话"""
    session_id = request.session_id
    
    if session_id not in qr_sessions:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")
    
    session = qr_sessions[session_id]
    
    # 检查是否过期
    if datetime.now() > session["expires_at"]:
        del qr_sessions[session_id]
        raise HTTPException(status_code=410, detail="会话已过期")
    
    # 检查是否已经绑定
    if session["is_bound"]:
        raise HTTPException(status_code=400, detail="会话已被绑定")
    
    # 绑定用户信息
    user_info = {
        "user_id": request.user_id,
        "user_name": request.user_name,
        "user_token": request.user_token
    }
    
    session["is_bound"] = True
    session["user_info"] = user_info
    
    # 保存到数据库
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT OR REPLACE INTO user_sessions 
        (session_id, user_id, user_name, user_token, created_at, expires_at, is_active)
        VALUES (?, ?, ?, ?, ?, ?, 1)
    ''', (
        session_id,
        request.user_id,
        request.user_name,
        request.user_token,
        datetime.now().isoformat(),
        session["expires_at"].isoformat()
    ))
    
    conn.commit()
    conn.close()
    
    return {"status": "success", "message": "绑定成功"}

# 新增POST请求的数据模型
class QRConfirmRequest(BaseModel):
    uuid: str
    doctor_id: str

@app.post("/api/qr-login/confirm")
async def confirm_qr_login(request: QRConfirmRequest):
    """确认二维码登录 - APP扫码后调用"""
    session_id = request.uuid
    doctor_id = request.doctor_id
    
    if session_id not in qr_sessions:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")
    
    session = qr_sessions[session_id]
    
    # 检查是否过期
    if datetime.now() > session["expires_at"]:
        del qr_sessions[session_id]
        raise HTTPException(status_code=410, detail="会话已过期")
    
    # 标记为已确认扫码并绑定医师信息
    session["is_confirmed"] = True
    session["confirmed_at"] = datetime.now()
    session["is_bound"] = True
    session["user_info"] = {
        "doctor_id": doctor_id,
        "login_time": datetime.now().isoformat()
    }
    
    print(f"二维码登录确认成功: {session_id}, 医师ID: {doctor_id}")
    print(f"当前会话状态: {session}")
    
    return {
        "status": "success",
        "message": "扫码确认成功",
        "uuid": session_id,
        "doctor_id": doctor_id
    }


@app.get("/api/qr-login/status")
async def get_qr_login_status(loginId: str = None):
    """检查二维码登录状态 - 网页前端轮询"""
    if not loginId:
        raise HTTPException(status_code=400, detail="缺少loginId参数")
    
    if loginId not in qr_sessions:
        raise HTTPException(status_code=404, detail="登录ID不存在或已过期")
    
    session = qr_sessions[loginId]
    
    # 检查是否过期
    if datetime.now() > session["expires_at"]:
        del qr_sessions[loginId]
        raise HTTPException(status_code=410, detail="登录ID已过期")
    
    # 检查扫码状态
    print(f"检查登录状态: {loginId}, 会话: {session}")
    
    if session.get("is_confirmed", False):
        # 扫码已确认，返回成功状态
        print(f"登录已确认: {loginId}")
        return {
            "status": "confirmed",
            "message": "扫码确认成功",
            "loginId": loginId,
            "confirmed_at": session.get("confirmed_at", datetime.now()).isoformat()
        }
    else:
        # 等待扫码
        print(f"等待扫码确认: {loginId}")
        return {
            "status": "waiting",
            "message": "等待扫码确认",
            "loginId": loginId
        }

def get_user_from_session(session_id: str):
    """从会话获取用户信息"""
    if session_id not in qr_sessions:
        return None
    
    session = qr_sessions[session_id]
    if not session["is_bound"] or not session["user_info"]:
        return None
    
    return session["user_info"]

def get_user_from_request(request: Request):
    """从请求中获取用户信息"""
    # 从请求头获取会话ID
    session_id = request.headers.get("X-Session-ID")
    if not session_id:
        return None
    
    return get_user_from_session(session_id)

@app.get("/api/health-suggestions", response_model=List[HealthSuggestion])
async def get_health_suggestions():
    """获取健康建议列表"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT id, title, content, author, tag, image_url, publish_time, user_id, user_ip
        FROM health_suggestions 
        ORDER BY id DESC
    ''')
    
    suggestions = []
    for row in cursor.fetchall():
        suggestions.append({
            "id": row['id'],
            "title": row['title'],
            "content": row['content'],
            "author": row['author'],
            "tag": row['tag'],
            "image_url": row['image_url'],
            "publish_time": row['publish_time'],
            "user_id": row['user_id'],
            "user_ip": row['user_ip']
        })
    
    conn.close()
    return suggestions

@app.get("/api/health-suggestions/{suggestion_id}", response_model=HealthSuggestion)
async def get_health_suggestion(suggestion_id: int):
    """获取单个健康建议"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT id, title, content, author, tag, image_url, publish_time, user_id, user_ip
        FROM health_suggestions 
        WHERE id = ?
    ''', (suggestion_id,))
    
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=404, detail="健康建议不存在")
    
    return {
        "id": row['id'],
        "title": row['title'],
        "content": row['content'],
        "author": row['author'],
        "tag": row['tag'],
        "image_url": row['image_url'],
        "publish_time": row['publish_time'],
        "user_id": row['user_id'],
        "user_ip": row['user_ip']
    }

@app.post("/api/health-suggestions", response_model=HealthSuggestion)
async def create_health_suggestion(
    request: Request,
    title: str = Form(...),
    content: str = Form(...),
    author: str = Form(...),
    tag: Optional[str] = Form(None),
    image: Optional[UploadFile] = File(None)
):
    """创建新的健康建议"""
    try:
        # 获取用户信息
        user_info = get_user_from_request(request)
        if not user_info:
            raise HTTPException(status_code=401, detail="请先扫码登录")
        
        # 验证必填字段
        if not title or not title.strip():
            raise HTTPException(status_code=400, detail="标题不能为空")
        if not content or not content.strip():
            raise HTTPException(status_code=400, detail="内容不能为空")
        if not author or not author.strip():
            raise HTTPException(status_code=400, detail="作者不能为空")
        
        image_url = None
        if image:
            # 检查文件类型
            if not image.content_type.startswith('image/'):
                raise HTTPException(status_code=400, detail="只支持图片文件")
            
            # 检查文件大小 (限制为2MB，与前端压缩后的大小限制一致)
            if image.size and image.size > 2 * 1024 * 1024:
                raise HTTPException(status_code=413, detail="图片文件过大，请压缩后重试")
            
            # 生成唯一文件名
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_extension = os.path.splitext(image.filename)[1] if image.filename else ".jpg"
            filename = f"health_suggestion_{timestamp}{file_extension}"
            file_path = UPLOAD_DIR / filename
            
            # 保存文件
            try:
                with open(file_path, "wb") as buffer:
                    shutil.copyfileobj(image.file, buffer)
                image_url = f"/uploads/{filename}"
                print(f"图片保存成功: {file_path}")
            except Exception as e:
                print(f"图片保存失败: {e}")
                raise HTTPException(status_code=500, detail=f"图片上传失败: {str(e)}")
        
        # 保存到数据库
        conn = get_db_connection()
        cursor = conn.cursor()
        
        publish_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        client_ip = request.client.host if request.client else "unknown"
        
        cursor.execute('''
            INSERT INTO health_suggestions (title, content, author, tag, image_url, publish_time, user_id, user_ip)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (title.strip(), content.strip(), author.strip(), tag.strip() if tag else None, image_url, publish_time, user_info["user_id"], client_ip))
        
        suggestion_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        print(f"健康建议创建成功: ID={suggestion_id}, 标题={title}, 用户ID={user_info['user_id']}")
        
        return {
            "id": suggestion_id,
            "title": title.strip(),
            "content": content.strip(),
            "author": author.strip(),
            "tag": tag.strip() if tag else None,
            "image_url": image_url,
            "publish_time": publish_time,
            "user_id": user_info["user_id"],
            "user_ip": client_ip
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"创建健康建议时发生错误: {e}")
        raise HTTPException(status_code=500, detail=f"服务器内部错误: {str(e)}")

@app.put("/api/health-suggestions/{suggestion_id}", response_model=HealthSuggestion)
async def update_health_suggestion(
    suggestion_id: int,
    request: Request,
    title: str = Form(...),
    content: str = Form(...),
    author: str = Form(...),
    tag: Optional[str] = Form(None),
    image: Optional[UploadFile] = File(None)
):
    """更新健康建议"""
    # 获取用户信息
    user_info = get_user_from_request(request)
    if not user_info:
        raise HTTPException(status_code=401, detail="请先扫码登录")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 检查建议是否存在并获取原作者信息
    cursor.execute('SELECT author, image_url, user_id FROM health_suggestions WHERE id = ?', (suggestion_id,))
    row = cursor.fetchone()
    
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="健康建议不存在")
    
    # 检查编辑权限：只有原作者可以编辑
    original_user_id = row['user_id']
    if user_info["user_id"] != original_user_id:
        conn.close()
        raise HTTPException(status_code=403, detail="只有原作者可以编辑此文章")
    
    # 处理图片上传
    image_url = row['image_url']
    if image:
        # 删除旧图片
        if image_url:
            old_image_path = BASE_DIR / image_url.lstrip('/')
            if old_image_path.exists():
                try:
                    os.remove(old_image_path)
                except:
                    pass
        
        # 保存新图片
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_extension = os.path.splitext(image.filename)[1] if image.filename else ".jpg"
        filename = f"health_suggestion_{timestamp}_{suggestion_id}{file_extension}"
        file_path = UPLOAD_DIR / filename
        
        try:
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(image.file, buffer)
            image_url = f"/uploads/{filename}"
        except Exception as e:
            conn.close()
            raise HTTPException(status_code=500, detail=f"图片上传失败: {str(e)}")
    
    # 更新建议
    publish_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    cursor.execute('''
        UPDATE health_suggestions 
        SET title = ?, content = ?, author = ?, tag = ?, image_url = ?, publish_time = ?
        WHERE id = ?
    ''', (title, content, author, tag, image_url, publish_time, suggestion_id))
    
    conn.commit()
    conn.close()
    
    return {
        "id": suggestion_id,
        "title": title,
        "content": content,
        "author": author,
        "tag": tag,
        "image_url": image_url,
        "publish_time": publish_time,
        "user_id": user_info["user_id"],
        "user_ip": request.client.host if request.client else "unknown"
    }

@app.delete("/api/health-suggestions/{suggestion_id}")
async def delete_health_suggestion(
    suggestion_id: int,
    request: Request
):
    """删除健康建议"""
    # 获取用户信息
    user_info = get_user_from_request(request)
    if not user_info:
        raise HTTPException(status_code=401, detail="请先扫码登录")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 获取建议信息
    cursor.execute('SELECT author, image_url, user_id FROM health_suggestions WHERE id = ?', (suggestion_id,))
    row = cursor.fetchone()
    
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="健康建议不存在")
    
    # 检查删除权限：只有原作者可以删除
    original_user_id = row['user_id']
    if user_info["user_id"] != original_user_id:
        conn.close()
        raise HTTPException(status_code=403, detail="只有原作者可以删除此文章")
    
    # 删除关联的图片
    if row['image_url']:
        image_path = BASE_DIR / row['image_url'].lstrip('/')
        if image_path.exists():
            try:
                os.remove(image_path)
            except:
                pass
    
    # 删除建议
    cursor.execute('DELETE FROM health_suggestions WHERE id = ?', (suggestion_id,))
    conn.commit()
    conn.close()
    
    return {"message": "健康建议删除成功"}

@app.get("/api/health-suggestions/search/{keyword}")
async def search_health_suggestions(keyword: str):
    """搜索健康建议"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT id, title, content, author, tag, image_url, publish_time, user_id, user_ip
        FROM health_suggestions 
        WHERE title LIKE ? OR content LIKE ? OR author LIKE ? OR tag LIKE ?
        ORDER BY id DESC
    ''', (f'%{keyword}%', f'%{keyword}%', f'%{keyword}%', f'%{keyword}%'))
    
    results = []
    for row in cursor.fetchall():
        results.append({
            "id": row['id'],
            "title": row['title'],
            "content": row['content'],
            "author": row['author'],
            "tag": row['tag'],
            "image_url": row['image_url'],
            "publish_time": row['publish_time'],
            "user_id": row['user_id'],
            "user_ip": row['user_ip']
        })
    
    conn.close()
    return results

@app.post("/api/upload-image")
async def upload_image(image: UploadFile = File(...)):
    """单独上传图片"""
    try:
        # 检查文件类型
        if not image.content_type.startswith('image/'):
            raise HTTPException(status_code=400, detail="只支持图片文件")
        
        # 检查文件大小 (限制为10MB)
        if image.size and image.size > 10 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="图片文件过大，请压缩后重试")
        
        # 生成唯一文件名
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_extension = os.path.splitext(image.filename)[1] if image.filename else ".jpg"
        filename = f"upload_{timestamp}{file_extension}"
        file_path = UPLOAD_DIR / filename
        
        # 保存文件
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(image.file, buffer)
        
        image_url = f"/uploads/{filename}"
        return {"image_url": image_url, "filename": filename}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"图片上传失败: {str(e)}")

if __name__ == "__main__":
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=8000
    ) 