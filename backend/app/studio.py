import os, re, uuid, subprocess, shutil
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

def make_router(auth):
    router=APIRouter(prefix="/api/studio",tags=["Druckstudio"])
    root=Path(os.getenv("AGPRINT_DATA_DIR","/app/data"))/"models"
    root.mkdir(parents=True,exist_ok=True)
    def path(uid,mid):
        if not re.fullmatch("[a-f0-9]{32}",mid): raise HTTPException(404,"Modell nicht gefunden")
        matches=[p for p in (root/str(uid)).glob(mid+".*") if p.suffix in (".stl",".3mf")]
        if not matches: raise HTTPException(404,"Modell nicht gefunden")
        return matches[0]
    @router.get("/models")
    def models(user=Depends(auth)):
        folder=root/str(user.id)
        return [{"id":p.stem,"format":p.suffix,"bytes":p.stat().st_size} for p in folder.glob("*") if re.fullmatch("[a-f0-9]{32}",p.stem) and p.suffix in (".stl",".3mf")]
    @router.post("/models")
    async def upload(file:UploadFile=File(...),user=Depends(auth)):
        suffix=Path(file.filename or "").suffix.lower()
        if suffix not in (".stl",".3mf"):raise HTTPException(422,"Nur STL oder 3MF")
        folder=root/str(user.id);folder.mkdir(parents=True,exist_ok=True)
        mid=uuid.uuid4().hex;dest=folder/(mid+suffix);size=0
        try:
            with dest.open("wb") as out:
                while chunk:=await file.read(1048576):
                    size+=len(chunk)
                    if size>50*1048576:raise HTTPException(413,"Maximal 50 MB")
                    out.write(chunk)
            if size==0:raise HTTPException(422,"Leere Datei")
            if suffix==".3mf" and dest.read_bytes()[:4]!=b"PK\\x03\\x04":raise HTTPException(422,"Ungültiger 3MF-Header")
            return {"id":mid,"format":suffix,"bytes":size}
        except Exception:
            dest.unlink(missing_ok=True);raise
    @router.get("/models/{mid}/download")
    def download(mid:str,user=Depends(auth)):
        p=path(user.id,mid);return FileResponse(p,filename=p.name)
    @router.delete("/models/{mid}")
    def delete(mid:str,user=Depends(auth)):
        path(user.id,mid).unlink();return {"ok":True}
    @router.get("/slicer")
    def capabilities(user=Depends(auth)):
        return {"available":bool(shutil.which("prusa-slicer")),"profile_present":Path("/app/profiles/printer.ini").is_file()}
    class SliceIn(BaseModel):
        model_id:str
    @router.post("/slice")
    def slice_file(data:SliceIn,user=Depends(auth)):
        source=path(user.id,data.model_id);profile=Path("/app/profiles/printer.ini")
        if not shutil.which("prusa-slicer"):raise HTTPException(503,"Slicer nicht installiert")
        if not profile.is_file():raise HTTPException(409,"Validiertes Profil fehlt: /app/profiles/printer.ini")
        target=source.with_suffix(".gcode")
        try:
            result=subprocess.run(["prusa-slicer","--load",str(profile),"--export-gcode","--output",str(target),str(source)],capture_output=True,text=True,timeout=300,cwd=str(source.parent))
        except subprocess.TimeoutExpired:
            target.unlink(missing_ok=True);raise HTTPException(504,"Slicing Timeout")
        if result.returncode or not target.is_file():raise HTTPException(422,"Slicing fehlgeschlagen: "+(result.stderr or result.stdout)[-400:])
        return {"ok":True,"bytes":target.stat().st_size,"warning":"Generischer G-Code, nicht für P2S validiert"}
    @router.get("/models/{mid}/gcode")
    def gcode(mid:str,user=Depends(auth)):
        p=path(user.id,mid).with_suffix(".gcode")
        if not p.is_file():raise HTTPException(404,"Kein G-Code")
        return FileResponse(p,filename=p.name)
    return router
