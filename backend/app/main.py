import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional
import jwt
from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path
from app.bambu_cloud import monitor
from app.studio import make_router
import re
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field
from pwdlib import PasswordHash
from sqlalchemy import create_engine, ForeignKey, String, Numeric, DateTime, Integer, Boolean, select, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker, Session

DATABASE_URL=os.environ['DATABASE_URL']
SECRET=os.environ['JWT_SECRET']
engine=create_engine(DATABASE_URL,pool_pre_ping=True)
SessionLocal=sessionmaker(bind=engine)
password_hash=PasswordHash.recommended()
class Base(DeclarativeBase): pass
class User(Base):
    __tablename__='users'
    id:Mapped[int]=mapped_column(primary_key=True)
    email:Mapped[str]=mapped_column(String(255),unique=True,index=True)
    password_hash:Mapped[str]=mapped_column(String(255))
    role:Mapped[str]=mapped_column(String(20),default='user')
class Filament(Base):
    __tablename__='filaments'
    id:Mapped[int]=mapped_column(primary_key=True)
    owner_id:Mapped[int]=mapped_column(ForeignKey('users.id'))
    brand:Mapped[str]=mapped_column(String(100))
    material:Mapped[str]=mapped_column(String(50))
    color:Mapped[str]=mapped_column(String(70))
    remaining_g:Mapped[Decimal]=mapped_column(Numeric(10,2))
    price_per_kg:Mapped[Decimal]=mapped_column(Numeric(10,2))
    spool_name:Mapped[str]=mapped_column(String(100),default='')
    ams_slot:Mapped[Optional[int]]=mapped_column(Integer,nullable=True)
class Order(Base):
    __tablename__='orders'
    id:Mapped[int]=mapped_column(primary_key=True)
    owner_id:Mapped[int]=mapped_column(ForeignKey('users.id'))
    customer:Mapped[str]=mapped_column(String(150))
    title:Mapped[str]=mapped_column(String(200))
    quantity:Mapped[int]=mapped_column(Integer)
    status:Mapped[str]=mapped_column(String(30),default='new')
    sale_price:Mapped[Decimal]=mapped_column(Numeric(10,2))
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))
class Printer(Base):
    __tablename__='printers'
    id:Mapped[int]=mapped_column(primary_key=True)
    owner_id:Mapped[int]=mapped_column(ForeignKey('users.id'))
    name:Mapped[str]=mapped_column(String(120))
    model:Mapped[str]=mapped_column(String(80),default='Bambu Lab P2S')
    has_ams:Mapped[bool]=mapped_column(Boolean,default=True)
    connection:Mapped[str]=mapped_column(String(30),default='cloud')

class Login(BaseModel):
    email:EmailStr
    password:str
class NewUser(Login):
    role:str='user'
class FilamentIn(BaseModel):
    brand:str=Field(min_length=1,max_length=100)
    material:str=Field(min_length=1,max_length=50)
    color:str=Field(min_length=1,max_length=70)
    remaining_g:float=Field(ge=0)
    price_per_kg:float=Field(ge=0)
    spool_name:str=''
    ams_slot:Optional[int]=Field(default=None,ge=1,le=16)
class OrderIn(BaseModel):
    customer:str=Field(min_length=1,max_length=150)
    title:str=Field(min_length=1,max_length=200)
    quantity:int=Field(ge=1)
    status:str='new'
    sale_price:float=Field(ge=0)
class PrinterIn(BaseModel):
    name:str=Field(min_length=1,max_length=120)
    model:str='Bambu Lab P2S'
    has_ams:bool=True
    connection:str='cloud'
class CostIn(BaseModel):
    grams:float=Field(ge=0)
    price_per_kg:float=Field(ge=0)
    hours:float=Field(ge=0)
    watts:float=Field(ge=0)
    electricity_per_kwh:float=Field(ge=0)
    machine_per_hour:float=Field(ge=0)
    failure_percent:float=Field(default=0,ge=0,le=99)
    sale_price:float=Field(ge=0)

app=FastAPI(title='AG-Print API',version='0.1.0')
app.add_middleware(CORSMiddleware,allow_origins=os.environ.get('CORS_ORIGINS','http://localhost:8086').split(','),allow_credentials=False,allow_methods=['*'],allow_headers=['*'])
@app.on_event('startup')
def setup():
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        email=os.environ['ADMIN_EMAIL'].lower()
        if not db.scalar(select(User).where(User.email==email)):
            db.add(User(email=email,password_hash=password_hash.hash(os.environ['ADMIN_PASSWORD']),role='admin'))
            db.commit()
        if monitor.serial and not db.scalar(select(Printer).where(Printer.owner_id==db.scalar(select(User.id).where(User.email==email)))):
            owner = db.scalar(select(User.id).where(User.email==email))
            db.add(Printer(owner_id=owner, name=os.getenv('BAMBU_PRINTER_NAME', 'Mein Bambu P2S'), model='Bambu Lab P2S', has_ams=True, connection='cloud'))
            db.commit()
    monitor.start()

@app.on_event('shutdown')
def teardown():
    monitor.stop()

def get_db():
    with SessionLocal() as db: yield db

def current_user(authorization:Optional[str]=Header(default=None),db:Session=Depends(get_db)):
    if not authorization or not authorization.startswith('Bearer '): raise HTTPException(401,'Not authenticated')
    try:
        payload=jwt.decode(authorization[7:],SECRET,algorithms=['HS256'])
        user=db.get(User,int(payload['sub']))
        if not user: raise ValueError('unknown user')
        return user
    except Exception: raise HTTPException(401,'Invalid or expired token')

def admin(user:User=Depends(current_user)):
    if user.role!='admin': raise HTTPException(403,'Admin only')
    return user

def own(db,cls,id,user):
    obj=db.get(cls,id)
    if not obj or (obj.owner_id!=user.id and user.role!='admin'): raise HTTPException(404,'Not found')
    return obj

def as_dict(obj):
    return {c.name:(float(v) if isinstance(v,Decimal) else v.isoformat() if isinstance(v,datetime) else v) for c in obj.__table__.columns for v in [getattr(obj,c.name)]}

@app.get('/api/health')
def health(): return {'ok':True,'version':'0.1.0'}
@app.post('/api/auth/login')
def login(data:Login,db:Session=Depends(get_db)):
    user=db.scalar(select(User).where(User.email==data.email.lower()))
    if not user or not password_hash.verify(data.password,user.password_hash): raise HTTPException(401,'Invalid credentials')
    token=jwt.encode({'sub':str(user.id),'exp':datetime.now(timezone.utc)+timedelta(hours=12)},SECRET,algorithm='HS256')
    return {'token':token,'user':{'id':user.id,'email':user.email,'role':user.role}}
@app.get('/api/auth/me')
def me(user:User=Depends(current_user)): return {'id':user.id,'email':user.email,'role':user.role}
@app.post('/api/users')
def add_user(data:NewUser,db:Session=Depends(get_db),_:User=Depends(admin)):
    if os.getenv('ENABLE_MULTIUSER', 'false').lower() != 'true': raise HTTPException(403, 'Single-user-Modus aktiv')
    if data.role not in ('admin','user'): raise HTTPException(400,'Invalid role')
    if db.scalar(select(User).where(User.email==data.email.lower())): raise HTTPException(409,'Email already exists')
    u=User(email=data.email.lower(),password_hash=password_hash.hash(data.password),role=data.role)
    db.add(u);db.commit();return {'id':u.id,'email':u.email,'role':u.role}
@app.get('/api/users')
def users(db:Session=Depends(get_db),_:User=Depends(admin)):
    return [{'id':u.id,'email':u.email,'role':u.role} for u in db.scalars(select(User)).all()]

@app.get('/api/filaments')
def filaments(db:Session=Depends(get_db),user:User=Depends(current_user)):
    return [as_dict(x) for x in db.scalars(select(Filament).where(Filament.owner_id==user.id).order_by(Filament.id.desc())).all()]
@app.post('/api/filaments')
def create_filament(data:FilamentIn,db:Session=Depends(get_db),user:User=Depends(current_user)):
    obj=Filament(owner_id=user.id,**data.model_dump());db.add(obj);db.commit();db.refresh(obj);return as_dict(obj)
@app.put('/api/filaments/{id}')
def update_filament(id:int,data:FilamentIn,db:Session=Depends(get_db),user:User=Depends(current_user)):
    obj=own(db,Filament,id,user)
    for k,v in data.model_dump().items():setattr(obj,k,v)
    db.commit();db.refresh(obj);return as_dict(obj)
@app.delete('/api/filaments/{id}')
def delete_filament(id:int,db:Session=Depends(get_db),user:User=Depends(current_user)):
    db.delete(own(db,Filament,id,user));db.commit();return {'ok':True}

@app.get('/api/orders')
def orders(db:Session=Depends(get_db),user:User=Depends(current_user)):
    return [as_dict(x) for x in db.scalars(select(Order).where(Order.owner_id==user.id).order_by(Order.id.desc())).all()]
@app.post('/api/orders')
def create_order(data:OrderIn,db:Session=Depends(get_db),user:User=Depends(current_user)):
    if data.status not in ('new','printing','done','cancelled'):raise HTTPException(400,'Invalid status')
    obj=Order(owner_id=user.id,**data.model_dump());db.add(obj);db.commit();db.refresh(obj);return as_dict(obj)
@app.put('/api/orders/{id}')
def update_order(id:int,data:OrderIn,db:Session=Depends(get_db),user:User=Depends(current_user)):
    if data.status not in ('new','printing','done','cancelled'):raise HTTPException(400,'Invalid status')
    obj=own(db,Order,id,user)
    for k,v in data.model_dump().items():setattr(obj,k,v)
    db.commit();db.refresh(obj);return as_dict(obj)
@app.delete('/api/orders/{id}')
def delete_order(id:int,db:Session=Depends(get_db),user:User=Depends(current_user)):
    db.delete(own(db,Order,id,user));db.commit();return {'ok':True}

@app.get('/api/printers')
def printers(db:Session=Depends(get_db),user:User=Depends(current_user)):
    return [dict(as_dict(x),status=monitor.snapshot()['connection_status'] if i==0 else 'not_configured',status_note=monitor.snapshot()['connection_note'] if i==0 else 'Kein Cloud-Adapter zugewiesen',telemetry=monitor.snapshot() if i==0 else None) for i,x in enumerate(db.scalars(select(Printer).where(Printer.owner_id==user.id).order_by(Printer.id)).all())]
@app.post('/api/printers')
def add_printer(data:PrinterIn,db:Session=Depends(get_db),user:User=Depends(current_user)):
    obj=Printer(owner_id=user.id,**data.model_dump());db.add(obj);db.commit();db.refresh(obj);return as_dict(obj)
@app.delete('/api/printers/{id}')
def delete_printer(id:int,db:Session=Depends(get_db),user:User=Depends(current_user)):
    db.delete(own(db,Printer,id,user));db.commit();return {'ok':True}

@app.post('/api/cost/calculate')
def calculate(data:CostIn,user:User=Depends(current_user)):
    filament=data.grams/1000*data.price_per_kg
    electricity=data.hours*data.watts/1000*data.electricity_per_kwh
    machine=data.hours*data.machine_per_hour
    base=filament+electricity+machine
    adjusted=base/(1-data.failure_percent/100)
    return {k:round(v,2) for k,v in {'filament':filament,'electricity':electricity,'machine':machine,'base_cost':base,'expected_cost':adjusted,'profit':data.sale_price-adjusted,'margin_percent':(data.sale_price-adjusted)/data.sale_price*100 if data.sale_price else 0}.items()}
@app.get('/api/dashboard')
def dashboard(db:Session=Depends(get_db),user:User=Depends(current_user)):
    f=db.scalars(select(Filament).where(Filament.owner_id==user.id)).all()
    o=db.scalars(select(Order).where(Order.owner_id==user.id)).all()
    p=db.scalars(select(Printer).where(Printer.owner_id==user.id)).all()
    return {'printers':len(p),'filament_spools':len(f),'filament_grams':round(sum(float(x.remaining_g) for x in f),1),'orders_open':sum(x.status in ('new','printing') for x in o),'sales_done':round(sum(float(x.sale_price)*x.quantity for x in o if x.status=='done'),2),'orders_total':len(o)}

class PrintControl(BaseModel):
    command: str
    value: Optional[int] = None

class SavedPrint(BaseModel):
    filename: str = Field(min_length=6, max_length=180)
    plate: int = Field(default=1, ge=1, le=16)
    use_ams: bool = True
    bed_leveling: bool = True

@app.post('/api/bambu/control')
def bambu_control(data: PrintControl, _:User=Depends(admin)):
    # Keep command vocabulary strictly bounded; arbitrary G-code is not accepted.
    if data.command in ('pause', 'resume', 'stop'):
        payload = {'param': ''}
        command = data.command
    elif data.command == 'speed':
        if data.value not in (1, 2, 3, 4):
            raise HTTPException(422, 'Geschwindigkeit 1-4 erforderlich')
        command = 'print_speed'
        payload = {'param': str(data.value)}
    elif data.command in ('nozzle_temp', 'bed_temp'):
        limit = 300 if data.command == 'nozzle_temp' else 110
        if data.value is None or not (0 <= data.value <= limit):
            raise HTTPException(422, f'Temperatur nur zwischen 0 und {limit} °C')
        # G-code method is firmware dependent; don't silently assume success.
        command = 'gcode_line'
        payload = {'param': f"{'M104' if data.command == 'nozzle_temp' else 'M140'} S{data.value}\n"}
    else:
        raise HTTPException(422, 'Unbekannter Steuerbefehl')
    try:
        return monitor.publish_command('print', command, **payload)
    except ConnectionError as exc:
        raise HTTPException(503, str(exc))

@app.post('/api/bambu/start-saved-print')
def start_saved_print(data: SavedPrint, _:User=Depends(admin)):
    # Must already exist as a sliced 3MF on the printer SD card.
    # No uploads/slicing via MQTT.
    if not re.fullmatch(r'[A-Za-z0-9_ .()\-]+\.gcode\.3mf', data.filename, re.IGNORECASE):
        raise HTTPException(422, 'Nur Dateinamen vorhandener .gcode.3mf-Dateien auf der Drucker-SD-Karte')
    if monitor.snapshot().get('print_state') not in ('IDLE', 'FINISH', 'FAILED'):
        raise HTTPException(409, 'Druckerstatus ist nicht bereit oder unbekannt')
    try:
        return monitor.publish_command('print', 'project_file',
            url=f'file:///sdcard/{data.filename}', file=data.filename,
            param=f'Metadata/plate_{data.plate}.gcode', project_id='0', profile_id='0',
            task_id='0', subtask_id='0', subtask_name=data.filename,
            use_ams=data.use_ams, bed_leveling=data.bed_leveling,
            flow_cali=False, vibration_cali=False, layer_inspect=False,
            timelapse=False, md5='', bed_type='auto')
    except ConnectionError as exc:
        raise HTTPException(503, str(exc))

@app.get('/api/bambu/status')
def bambu_status(user:User=Depends(current_user)):
    return monitor.snapshot()

app.include_router(make_router(current_user))

# Single-container image: serve React build from the API service.
STATIC_DIR = Path('/app/static')
if STATIC_DIR.is_dir():
    app.mount('/assets', StaticFiles(directory=STATIC_DIR / 'assets'), name='assets')

    @app.get('/', include_in_schema=False)
    def spa_home():
        return FileResponse(STATIC_DIR / 'index.html')

    @app.get('/{path:path}', include_in_schema=False)
    def spa_fallback(path: str):
        if path.startswith('api/'):
            raise HTTPException(status_code=404)
        file_path = (STATIC_DIR / path).resolve()
        if file_path.is_relative_to(STATIC_DIR.resolve()) and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(STATIC_DIR / 'index.html')
