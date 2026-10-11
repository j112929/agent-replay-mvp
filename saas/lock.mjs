import {mkdirSync,openSync,writeFileSync,readFileSync,unlinkSync,closeSync} from 'node:fs';
import {join} from 'node:path';
import {hostname} from 'node:os';
import {randomUUID} from 'node:crypto';

// This file store requires one controller on one host, not a shared network disk.
export function controllerLock(directory) {
  mkdirSync(directory,{recursive:true,mode:0o700});
  const path=join(directory,'controller.lock'), identity=randomUUID();
  for(let attempt=0;attempt<2;attempt++) {
    try {
      const fd=openSync(path,'wx',0o600);
      writeFileSync(fd,JSON.stringify({pid:process.pid,host:hostname(),identity}));closeSync(fd);
      return () => {try {if(JSON.parse(readFileSync(path,'utf8')).identity===identity)unlinkSync(path);}catch{}};
    } catch(error) {
      if(error.code!=='EEXIST')throw new Error('Cannot acquire controller storage lock');
      let previous;
      try {previous=JSON.parse(readFileSync(path,'utf8'));} catch{throw new Error('Invalid controller lock; inspect it before restarting');}
      if(previous.host!==hostname() || !Number.isInteger(previous.pid) || previous.pid<1)throw new Error('Storage is locked; shared-host controllers are unsupported');
      try {process.kill(previous.pid,0);}catch(e){if(e.code==='ESRCH'){unlinkSync(path);continue;}}
      throw new Error('A controller is already using this data directory');
    }
  }
  throw new Error('Cannot acquire controller storage lock');
}
