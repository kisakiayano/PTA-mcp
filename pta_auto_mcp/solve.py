#!/usr/bin/env python3
import base64
import hashlib
import io
import json
import random
import re
import time
from typing import ClassVar

import numpy as np
import requests

try:
    import cv2
    _HAS_CV2 = True
except ImportError:
    cv2 = None
    _HAS_CV2 = False
from PIL import Image, ImageFilter

API="https://turing.captcha.qcloud.com"
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36"

class TCaptcha:
    last_locate: ClassVar[dict] = {}
    def __init__(self, aid, entry_url, proxy=None):
        self.aid, self.entry = aid, entry_url
        self.s = requests.Session()
        self.s.headers.update({"User-Agent":UA,"Referer":"https://pintia.cn/","Accept":"*/*"})
        if proxy: self.s.proxies={"http":proxy,"https":proxy}
        self.stat={}
    def prehandle(self):
        t0=time.time()
        sid=str(int(time.time()*10**9)); tkid=str(random.randint(10**8,10**9-1))
        p={"aid":self.aid,"protocol":"https","accver":"1","showtype":"popup",
           "ua":base64.b64encode(UA.encode()).decode(),"noheader":"1","fb":"1","aged":"0",
           "enableAged":"0","enableDarkMode":"0","grayscale":"1","clientype":"2","cap_cd":"","uid":"",
           "lang":"zh-cn","entry_url":self.entry,"elder_captcha":"0","js":"/tcaptcha-frame.ba387dd1.js",
           "login_appid":"","wb":"2","tkid":tkid,"subsid":"1",
           "callback":f"_aq_{random.randint(10**5, 10**6 - 1)}","sess":""}
        d=json.loads(re.match(r'^[^(]*\((.*)\)\s*$',
            self.s.get(API+"/cap_union_prehandle",params=p,timeout=15).text,re.DOTALL).group(1))
        self.stat['prehandle']=round((time.time()-t0)*1000)
        return d, sid, tkid
    def images(self, show, tkid):
        t0=time.time()
        b=self.s.get(API+show['bg_elem_cfg']['img_url']+"&tkid="+tkid,timeout=15).content
        p=self.s.get(API+show['sprite_url']+"&tkid="+tkid,timeout=15).content
        self.stat['images']=round((time.time()-t0)*1000)
        if _HAS_CV2:
            return (cv2.imdecode(np.frombuffer(b,np.uint8),cv2.IMREAD_UNCHANGED),
                    cv2.imdecode(np.frombuffer(p,np.uint8),cv2.IMREAD_UNCHANGED))
        return (np.array(Image.open(io.BytesIO(b)).convert('RGB')),
                np.array(Image.open(io.BytesIO(p)).convert('RGBA')))
    @staticmethod
    def locate(bg, sp, p1):
        """缺口定位: 拼图块 alpha 轮廓在背景上的局部暗度对比, 取对比度最大处。
        cv2 存在则用 cv2.dilate/cvtColor, 否则用 Pillow MaxFilter + 'L' 转换(结果一致)。"""
        t0=time.time()
        sx,sy=p1['sprite_pos']; w,h=p1['size_2d']; ix,iy=p1['init_pos']
        m=sp[sy:sy+h, sx:sx+w, 3]>128
        if m.sum()<100: return ix, iy, 0.0, round((time.time()-t0)*1000)
        if _HAS_CV2:
            ring=(cv2.dilate(m.astype(np.uint8),np.ones((9,9),np.uint8))>0)&~m
            g=cv2.cvtColor(bg[:,:,:3],cv2.COLOR_BGR2GRAY).astype(np.float32)
        else:
            dil=np.array(Image.fromarray(m.astype(np.uint8)*255,'L').filter(ImageFilter.MaxFilter(9)))>0
            ring=dil&~m
            g=np.array(Image.fromarray(bg[:,:,:3]).convert('L'),dtype=np.float32)
        pg=(cv2.cvtColor(sp[sy:sy+h, sx:sx+w, :3],cv2.COLOR_BGR2GRAY).astype(np.float32) if _HAS_CV2
            else np.array(Image.fromarray(sp[sy:sy+h, sx:sx+w, :3]).convert('L'),dtype=np.float32))
        xs=[];A=[];B=[]
        for x in range(50, min(553, bg.shape[1]-w+1)):
            win=g[iy:iy+h, x:x+w]
            if win.shape!=m.shape: break
            xs.append(x)
            A.append(-(win[m].mean()-win[ring].mean()))          # 信号1: 缺口更暗
            a=pg[m].ravel(); b=win[m].ravel()                    # 信号2: 拼图块内容与背景相关性
            B.append(0.0 if a.std()<1e-6 or b.std()<1e-6 else float(np.corrcoef(a,b)[0,1]))
        if not xs: return ix, iy, 0.0, round((time.time()-t0)*1000)
        A=np.asarray(A); B=np.asarray(B)
        iA=int(A.argmax()); iB=int(B.argmax())
        xA,xB=xs[iA],xs[iB]
        TCaptcha.last_locate={'xA':xA,'xB':xB,'contrast':float(A[iA]),'corr':float(B[iB]),
                              'agree':abs(xA-xB)<=4,'n':len(xs)}
        return xA, iy, float(A[iA]), round((time.time()-t0)*1000)
    @staticmethod
    def pow_solve(prefix, target, limit=2_000_000):
        t0=time.time()
        for i in range(limit):
            if hashlib.md5((prefix+str(i)).encode()).hexdigest()==target:
                return i, int((time.time()-t0)*1000)
        return None, int((time.time()-t0)*1000)
    def verify(self, sess, tkid, ans, x, y):
        body={"collect":"","tlg":"0","eks":"","sess":sess,
              "ans":json.dumps([{"elem_id":1,"type":ans,"data":f"{x},{y}"}],separators=(',',':')),
              "tkid":tkid}
        return self.s.post(API+"/cap_union_new_verify",data=body,timeout=15,
            headers={"Content-Type":"application/x-www-form-urlencoded; charset=UTF-8",
                     "Origin":"https://turing.captcha.gtimg.com",
                     "Accept":"application/json, text/javascript, */*; q=0.01"}).json()
    def solve(self, retries=3, min_contrast=45.0, refresh=3):
        """最小链路: prehandle -> bg -> sprite -> verify (4 请求)。"""
        t_all=time.time(); tries=0
        d, _, tkid = self.prehandle()
        show=d['data']['dyn_show_info']; pw=d['data']['comm_captcha_cfg'].get('pow_cfg') or {}
        sess=d['sess']
        p1 = next(f for f in show['fg_elem_list'] if f.get('id') == 1)
        ans_t=p1['move_cfg']['data_type'][0]; y=p1['init_pos'][1]
        x=None
        for _ in range(refresh):                              # 置信度不足则换题重取, 避免误答
            bg,sp=self.images(show, tkid)
            x,_,contrast,cv_ms = self.locate(bg, sp, p1)
            if contrast>=min_contrast and TCaptcha.last_locate.get('agree'): break
            nd,_,_=self.prehandle(); sess=nd['sess']; show=nd['data']['dyn_show_info']; tkid=str(random.randint(10**8,10**9-1))
            p1 = next(f for f in show['fg_elem_list'] if f.get('id') == 1); y=p1['init_pos'][1]
        _, pow_ms = self.pow_solve(pw.get('prefix',''), pw.get('md5',''))
        while tries < retries:
            tries+=1
            r=self.verify(sess, tkid, ans_t, x, y)
            if r.get('errorCode')=="0":
                self.stat.update({"cv_ms":cv_ms,"contrast":round(contrast,1),"pow_ms":pow_ms,
                                  "x":x,"tries":tries,"total_ms":round((time.time()-t_all)*1000)})
                return True, r
            sess=r.get('sess') or sess
            if tries<retries: time.sleep(0.6)
        self.stat.update({"cv_ms":cv_ms,"contrast":round(contrast,1),"pow_ms":pow_ms,"x":x,
                          "tries":tries,"total_ms":round((time.time()-t_all)*1000)})
        return False, r
