/* Classic script for file:// and WKWebView. Coordinates inside the simulation
   are upright source pixels; speed is expressed in image heights per second. */
(() => {
  'use strict';
  const occupied = new WeakMap();
  const mod = (x, n) => ((x % n) + n) % n;
  const clamp = (x, lo, hi) => Math.min(hi, Math.max(lo, x));
  function generator(seed) {
    let state = seed >>> 0;
    return () => {
      state = (Math.imul(1664525, state) + 1013904223) >>> 0;
      return state / 4294967296;
    };
  }
  async function image(src) {
    const im = new Image();
    await new Promise((resolve, reject) => {
      im.onload = resolve;
      im.onerror = () => reject(Error('无法读取图层 ' + src.slice(0, 60)));
      im.src = src;
    });
    return im;
  }
  async function pixels(uri, w, h) {
    const im = await image(uri);
    if (im.width !== w || im.height !== h) throw Error('采样图尺寸不匹配');
    const c = document.createElement('canvas');
    c.width = w;
    c.height = h;
    const ctx = c.getContext('2d');
    ctx.drawImage(im, 0, 0);
    const bytes = ctx.getImageData(0, 0, w, h).data;
    c.width = c.height = 1;
    return bytes;
  }

  class Field {
    constructor(config, grid, maps) {
      [this.w, this.h] = config.size;
      this.config = config;
      this.grid = grid;
      this.maps = maps;
      this.metric = config.occlusion.method === 'relative-inverse-depth';
    }
    offset(x, y) {
      if (x < 0 || y < 0 || x >= this.w || y >= this.h) return -1;
      return 4 * (Math.floor(y) * this.w + Math.floor(x));
    }
    visible(i, z) {
      return i >= 0 && !(this.grid[i+1] & 1) && z * 255 >= this.grid[i];
    }
    wet(i) {
      return this.rain ? this.rain[i+2] > 0 : !!(this.grid[i+1] & 4);
    }
    permission(i) {
      if (i < 0 || (this.grid[i+1] & 25)) return '';
      const pool = !!this.grid[i+2];
      if (!this.rain) return this.wet(i) || pool ? 'ring' : '';
      if (this.wet(i)) return this.rain[i+1] ? 'ring' : '';
      return this.rain[i] ? (pool ? 'ring' : 'splash') : '';
    }
    // Trace the actual slanted leading tip once. Both seek and live playback
    // use this same first intersection. Never connect points across a wrap.
    trace(p) {
      const slope = p.vx / p.vy;
      const step = 1 / Math.max(1, Math.abs(slope));
      let previousX = p.x0;
      let prev = this.offset(p.x0, 0);
      const landingHeight = this.h*(.18 + .76*p.depth);
      for (let y = step; y < this.h; y += step) {
        const x = mod(p.x0 + y * slope, this.w);
        const i = this.offset(x, y);
        if (i < 0) continue;
        if (Math.abs(x-previousX) > this.w/2) prev = i;
        previousX = x;
        const crossed = this.metric
          ? this.grid[prev] < p.z*255 && this.grid[i] >= p.z*255 && this.grid[i]-p.z*255 <= 8
          : y >= landingHeight && !!this.permission(i);
        if (crossed && !(this.grid[i+1] & 16)) {
          return {x, y, t:y/p.vy, kind:this.permission(i), z:p.z};
        }
        prev = i;
      }
      return null;
    }
    set(name, wind, quality) {
      const profile = this.config.profiles[name];
      this.profile = profile;
      this.rain = this.maps[name];
      this.drops = [];
      this.drains = this.config.drains[name] || [];
      if (profile.kind === 'clear') return;
      const random = generator(this.config.seed);
      const n = Math.ceil(profile.count * profile.gain * quality);
      for (let k = 0; k < n; k++) {
        const near = random() < profile.near;
        const depth = near ? .66 + random()*.34 : .09 + random()*.54;
        const z = this.metric ? depth : near ? .85 : .25;
        const speed = (.55 + depth*.83) * (.85 + random()*.3);
        const vy = this.h * profile.fall * speed;
        const vx = this.h * wind * speed;
        const p = {z, depth, near, vx, vy, x0:random()*this.w,
                   phase:random(), detail:random(), hit:null};
        if (profile.kind === 'rain') p.hit = this.trace(p);
        p.period = (p.hit ? p.hit.t : this.h/vy) + (profile.kind === 'rain' ? .42 : 0);
        this.drops.push(p);
      }
    }
    sample(time) {
      const air = [], impacts = [];
      const kind = this.profile.kind;
      for (const p of this.drops) {
        const t = mod(time + p.phase*p.period, p.period);
        if (p.hit && t >= p.hit.t) {
          if (p.hit.kind) impacts.push({...p.hit, age:(t-p.hit.t)/.42});
          continue;
        }
        const y = t*p.vy;
        if (y >= this.h) continue;
        const sway = kind === 'snow' ? this.h*.008*Math.sin(t*1.2 + p.detail*29) : 0;
        air.push({...p, kind, x:mod(p.x0 + t*p.vx + sway, this.w), y});
      }
      for (const [k, pair] of this.drains.entries()) {
        const [[u,v],[endU,endV]] = pair;
        const x0 = u*this.w, y0 = v*this.h, x = endU*this.w, y = endV*this.h;
        const flight = Math.sqrt((endV-v)/1.7);
        const t = mod(time + k*.37, flight + 1.5);
        const a = this.offset(x0,y0), b = this.offset(x,y);
        if (t < flight) {
          const f = (t/flight)**2;
          const z = (this.grid[a]*(1-f) + this.grid[b]*f)/255;
          air.push({kind:'drip', x:x0+(x-x0)*f, y:y0+(y-y0)*f, z, depth:z});
        } else if (t < flight+.42) {
          impacts.push({kind:this.wet(b) || this.grid[b+2] ? 'ring' : 'splash',
                        x, y, age:(t-flight)/.42, paired:true});
        }
      }
      return {air, impacts};
    }
  }

  class Painter {
    constructor(canvas, field) {
      this.canvas = canvas;
      this.ctx = canvas.getContext('2d');
      this.field = field;
    }
    viewport(w, h, zoom, resolution) {
      const c = this.canvas;
      c.width = Math.max(1, Math.round(w*resolution));
      c.height = Math.max(1, Math.round(h*resolution));
      this.scale = Math.max(w/this.field.w, h/this.field.h)*zoom;
      this.ox = (w-this.field.w*this.scale)/2;
      this.oy = (h-this.field.h*this.scale)/2;
      this.rx = c.width/w;
      this.ry = c.height/h;
    }
    // Clip only the sprite footprint. Test destination pixel centers against the
    // original UV grid, including stroke width at a coastline or silhouette.
    clip(left, top, right, bottom, allow) {
      const {ctx, canvas, ox, oy, scale, rx, ry, field} = this;
      const x0 = Math.max(0, Math.floor(left)), y0 = Math.max(0, Math.floor(top));
      const x1 = Math.min(canvas.width, Math.ceil(right)), y1 = Math.min(canvas.height, Math.ceil(bottom));
      if (x0 >= x1 || y0 >= y1) return false;
      const path = new Path2D();
      let kept = 0;
      for (let y = y0; y < y1; y++) {
        const sy = ((y+.5)/ry-oy)/scale;
        let start = -1;
        for (let x = x0; x <= x1; x++) {
          const good = x < x1 && allow(field.offset(((x+.5)/rx-ox)/scale, sy));
          if (good && start < 0) start = x;
          if (!good && start >= 0) {
            path.rect(start, y, x-start, 1);
            kept += x-start;
            start = -1;
          }
        }
      }
      if (!kept) return false;
      if (kept < (x1-x0)*(y1-y0)) ctx.clip(path);
      return true;
    }
    draw(frame) {
      const {ctx, canvas, scale, ox, oy, rx, ry, field} = this;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      const unit = field.h/750 * scale * rx;
      const alpha = field.profile.opacity || 0;
      const screen = p => [(ox+p.x*scale)*rx, (oy+p.y*scale)*ry];
      for (const p of frame.impacts) {
        const [x,y] = screen(p);
        const a = (1.2 + p.age*8.5)*unit, b = a*.29;
        ctx.save();
        const allowed = i => {
          if (i < 0 || field.grid[i+1] & 1) return false;
          if (!p.paired) return field.permission(i) === p.kind;
          return p.kind === 'ring' ? field.wet(i) || !!field.grid[i+2] : !field.wet(i);
        };
        const pad = Math.max(2, 2*unit);
        if (this.clip(x-a-pad, y-a-pad, x+a+pad, y+b+pad, allowed)) {
          ctx.strokeStyle = 'rgba(214,241,255,' + ((1-p.age)*.78) + ')';
          ctx.lineWidth = Math.max(.65, unit);
          ctx.beginPath();
          if (p.kind === 'ring') ctx.ellipse(x,y,a,b,0,0,Math.PI*2);
          else {
            ctx.moveTo(x-a*.65,y-a*.55); ctx.lineTo(x-a*.28,y-b);
            ctx.moveTo(x+a*.25,y-b); ctx.lineTo(x+a*.7,y-a*.73);
          }
          ctx.stroke();
        }
        ctx.restore();
      }
      for (const p of frame.air) {
        const [x,y] = screen(p);
        const size = Math.max(.5, unit*(.48 + 2.3*p.depth*p.depth));
        const detailed = p.kind === 'snow' && p.near && p.detail > .75;
        const r = detailed ? size*1.6 : size;
        const length = p.kind === 'wind' ? .075 : p.kind === 'rain' ? .022 : 0;
        const dx = (p.vx || 0)*length*scale*rx, dy = (p.vy || 0)*length*scale*ry;
        const pad = r + Math.max(unit, 1) + 1;
        ctx.save();
        if (this.clip(Math.min(x,x-dx)-pad,Math.min(y,y-dy)-pad,
                      Math.max(x,x-dx)+pad,Math.max(y,y-dy)+pad,i=>field.visible(i,p.z))) {
          ctx.globalAlpha = alpha*(.42+p.depth*.55);
          ctx.fillStyle = '#f5faff';
          ctx.strokeStyle = p.kind === 'wind' ? '#efe3c3' : '#e3f2ff';
          ctx.lineWidth = Math.max(.64, unit*(.55+p.depth*.8));
          ctx.lineCap = 'round';
          ctx.beginPath();
          if (detailed) {
            for (let j=0; j<3; j++) {
              const angle = j*Math.PI/3+p.detail;
              const ax = Math.cos(angle)*r, ay = Math.sin(angle)*r;
              ctx.moveTo(x-ax,y-ay); ctx.lineTo(x+ax,y+ay);
            }
            ctx.stroke();
          } else if (p.kind === 'snow' || p.kind === 'drip') {
            ctx.ellipse(x,y,size,p.kind === 'drip' ? size*1.2 : size,0,0,Math.PI*2);
            ctx.fill();
          } else {
            ctx.moveTo(x-dx,y-dy); ctx.lineTo(x,y); ctx.stroke();
          }
        }
        ctx.restore();
      }
    }
  }

  async function create(root, scene, opts={}) {
    const old = occupied.get(root);
    if (old) old.destroy();
    const {config, maps} = scene;
    const [w,h] = config.size;
    const box = document.createElement('div');
    box.style.cssText='position:absolute;inset:0;overflow:hidden;isolation:isolate;pointer-events:none';
    root.appendChild(box);
    let dead = false;
    const loading = {destroy() {dead = true; box.remove();}};
    occupied.set(root, loading);
    try {
      const relative = name => new URL(name, opts.baseURL || document.baseURI).href;
      const layer = async name => {
        const im = await image(relative(name));
        im.style.cssText='position:absolute;max-width:none;max-height:none;pointer-events:none';
        return im;
      };
      const grid = await pixels(maps.scene,w,h);
      const rain = {};
      for (const key of Object.keys(maps.rain)) rain[key] = await pixels(maps.rain[key],w,h);
      const photo = await layer(config.photo);
      box.appendChild(photo);
      const still = [];
      for (const spec of config.materials) {
        const im = await layer(spec.path);
        im.style.mixBlendMode = spec.blend;
        box.appendChild(im);
        still.push([im,spec]);
      }
      const canvas = document.createElement('canvas');
      canvas.style.cssText='position:absolute;inset:0;width:100%;height:100%';
      box.appendChild(canvas);
      const caption = config.caption ? await layer(config.caption) : null;
      if (caption) box.appendChild(caption);
      if (dead) throw Error('动画创建已取消');
      const field = new Field(config,grid,rain);
      const painter = new Painter(canvas,field);
      const reduced = matchMedia('(prefers-reduced-motion: reduce)');
      const fps = clamp(opts.fps || config.fps, 1, 120);
      const quality = clamp(opts.quality ?? 1,.05,1);
      const resolution = clamp(opts.resolution ?? 1,.2,2);
      let weather, wind, zoom = 1, elapsed = 0, last = null, due = null, raf = null;
      let paused = !!opts.paused;
      function render() {
        const frame = field.sample(elapsed);
        painter.draw(frame);
        return frame;
      }
      function stop() {
        if (raf !== null) cancelAnimationFrame(raf);
        raf = last = due = null;
      }
      function active() {
        return !dead && !paused && !document.hidden && !reduced.matches && field.profile.kind !== 'clear';
      }
      function tick(now) {
        raf = null;
        if (!active()) return;
        if (last === null) { last = now; due = now; }
        if (now + .5 >= due) {
          elapsed += (now-last)/1000;
          last = now;
          due += (Math.floor(Math.max(0, now+.5-due)/(1000/fps))+1)*1000/fps;
          render();
          if (opts.onFrame) opts.onFrame({time:elapsed, timestamp:now});
        }
        if (active() && raf === null) raf = requestAnimationFrame(tick);
      }
      function schedule() {
        stop();
        if (active()) raf = requestAnimationFrame(tick);
      }
      function setWeather(name, options={}) {
        if (dead) return;
        const profile = config.profiles[name];
        if (!profile) throw RangeError('未知天气 ' + name);
        const speed = options.wind ?? profile.wind ?? 0;
        if (!Number.isFinite(speed) || Math.abs(speed)>2) throw RangeError('wind 范围为 -2 至 2');
        if (weather === name && wind === speed) return;
        weather = name; wind = speed; elapsed = 0;
        field.set(name,wind,quality);
        for (const [im,spec] of still) {
          im.style.display = !spec.weather || spec.weather === name || (Array.isArray(spec.weather) && spec.weather.includes(name)) ? '' : 'none';
        }
        render(); schedule();
      }
      function resize(width=root.clientWidth,height=root.clientHeight,factor=zoom) {
        if (dead) return;
        if (![width,height,factor].every(Number.isFinite) || factor < 1 || width < 0 || height < 0)
          throw RangeError('视口尺寸或缩放无效');
        zoom = factor;
        width = Math.max(width,1); height = Math.max(height,1);
        box.style.width = width+'px'; box.style.height = height+'px';
        painter.viewport(width,height,zoom,resolution);
        for (const im of [photo,...still.map(pair=>pair[0]),caption].filter(Boolean)) {
          Object.assign(im.style,{left:painter.ox+'px',top:painter.oy+'px',
            width:w*painter.scale+'px',height:h*painter.scale+'px'});
        }
        if (weather) render();
      }
      function onResize() { resize(); }
      const observer = new ResizeObserver(onResize);
      observer.observe(root);
      document.addEventListener('visibilitychange',schedule);
      reduced.addEventListener('change',schedule);
      const api = {setWeather,resize,canvas,
        pause() {paused=true; stop();}, resume() {if (!dead && paused) {paused=false; schedule();}},
        sample(seconds) {
          if (dead || !Number.isFinite(seconds) || seconds<0) throw RangeError('无效的采样时间或实例');
          elapsed=seconds;
          const result=render();
          schedule();
          return result;
        },
        destroy() {
          dead=true; stop(); observer.disconnect();
          document.removeEventListener('visibilitychange',schedule);
          reduced.removeEventListener('change',schedule);
          box.remove();
          if (occupied.get(root)===api) occupied.delete(root);
        }
      };
      resize(); setWeather(config.default_weather);
      occupied.set(root,api);
      return api;
    } catch (error) {
      loading.destroy();
      if (occupied.get(root)===loading) occupied.delete(root);
      throw error;
    }
  }
  window.WeatherParticleMotion = {create, Field, Painter};
})();
