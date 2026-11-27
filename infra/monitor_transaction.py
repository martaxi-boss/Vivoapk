"""Two-file internal monitor provisioning with exact rollback and no secret logs."""
import json,os,hashlib

def atomic(path,data,mode,uid,gid):
    temp=path.with_name(path.name+'.monitor-r168.tmp')
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(data);f.flush();os.fsync(f.fileno());os.fchmod(f.fileno(),mode);os.fchown(f.fileno(),uid,gid)
        os.replace(temp,path)
        d=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(d)
        finally:os.close(d)
    finally:
        if temp.exists():temp.unlink()

def configure(envfile,keyfile,backup,old,new,meta,key,uid,gid,restart,verify):
    if os.path.lexists(keyfile) or envfile.read_bytes()!=old:raise RuntimeError('original_changed')
    journal={'phase':'prepared','old_env_sha256':hashlib.sha256(old).hexdigest(),'mode':meta[0],'uid':meta[1],'gid':meta[2]}
    atomic(backup/'native-media.env.before',old,0o600,os.getuid(),os.getgid())
    def phase(value):
        journal['phase']=value;atomic(backup/'journal.json',json.dumps(journal).encode(),0o600,os.getuid(),os.getgid())
    phase('prepared');created=False;changed=False;inode=None
    try:
        fd=os.open(keyfile,os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_WRONLY,0o400)
        with os.fdopen(fd,'wb') as f:
            created=True;inode=os.fstat(f.fileno()).st_ino
            f.write(key);f.flush();os.fsync(f.fileno());os.fchown(f.fileno(),uid,gid)
        if envfile.read_bytes()!=old:raise RuntimeError('original_changed')
        atomic(envfile,new,*meta);changed=True;phase('installing');restart();verify();phase('committed')
        return {'transaction_result':'committed','automatic_rollback':False}
    except (Exception,KeyboardInterrupt) as error:
        phase('restoring')
        if changed:
            if envfile.read_bytes()!=new:raise RuntimeError('unrelated_env_change') from None
            atomic(envfile,old,*meta)
        if created:
            if keyfile.is_symlink() or keyfile.lstat().st_ino!=inode or keyfile.read_bytes()!=key:raise RuntimeError('unrelated_key_change') from None
            keyfile.unlink()
        if changed:restart()
        phase('rolled_back')
        return {'transaction_result':'rolled_back','automatic_rollback':True,'failure_type':type(error).__name__}
