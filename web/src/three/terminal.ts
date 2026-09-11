import type { FloorArmState, FloorState } from './floor'
import type { TerminalParts } from './models'

/** Dense financial workstation. All numerical readouts come from the current replay. */
export function drawTerminal(terminal:TerminalParts, arm:FloorArmState, state:FloorState, time:number, flash:number) {
  const c=terminal.canvas.getContext('2d')!
  const w=terminal.canvas.width,h=terminal.canvas.height
  const orange='#ff9d21', yellow='#ffd569', cyan='#64cfff', green='#5be5a1',red='#ff6673',muted='#8196ab'
  const text=(value:string,x:number,y:number,color='#cbd8e5',size=15)=>{c.font=`${size}px Consolas, monospace`;c.fillStyle=color;c.fillText(value,x,y)}
  const rect=(x:number,y:number,width:number,height:number,color:string)=>{c.fillStyle=color;c.fillRect(x,y,width,height)}
  const panel=(x:number,y:number,width:number,height:number,label:string)=>{rect(x,y,width,height,'#030609');c.strokeStyle='#354554';c.lineWidth=1;c.strokeRect(x,y,width,height);rect(x,y,width,23,'#172635');text(label,x+7,y+16,orange,13)}
  c.clearRect(0,0,w,h);rect(0,0,w,h,'#020406')
  rect(0,0,w,27,orange);text('FRUIT FLY  /  PROFESSIONAL',9,19,'#080a0a',16)
  text(state.live?'LIVE SESSION':state.engine==='neural'?'NEURAL REPLAY':'SYNTHETIC SANDBOX',w-240,19,'#080a0a',14)
  const tabs=['1  MONITOR','2  CHART','3  EXECUTIONS','4  NEURAL','5  PORTFOLIO']
  tabs.forEach((label,i)=>{rect(5+i*202,33,197,25,i===0?'#264973':'#111c29');text(label,12+i*202,50,i===0?'#ffffff':yellow,14)})
  rect(6,65,w-12,30,'#0c1520');text(`${state.product}  <EQUITY / MARKET>   ${arm.name.toUpperCase()}`,14,86,orange,17)
  text('PAPER',w-75,86,green,15)
  const left=12,top=105,chartW=618,chartH=260,right=640,rightW=w-right-12
  panel(left,top,chartW,chartH,'PORTFOLIO / INTRADAY EQUITY')
  text(arm.equity.toFixed(2),24,165,yellow,32)
  text(`${arm.returnPct>=0?'+':''}${arm.returnPct.toFixed(3)}%`,205,165,arm.returnPct>=0?green:red,23)
  text(`MID  ${state.mid.toFixed(2)}`,405,163,cyan,18)
  const values=arm.curve.length?arm.curve:[state.initialCapital]
  const low=Math.min(...values,state.initialCapital),high=Math.max(...values,state.initialCapital),span=high-low||1
  const x=(i:number)=>26+i/Math.max(1,values.length-1)*530
  const y=(v:number)=>331-(v-low)/span*139
  for(let i=0;i<5;i++) {
    const yy=191+i*35;c.strokeStyle='#182a36';c.beginPath();c.moveTo(22,yy);c.lineTo(619,yy);c.stroke()
    text((high-span*i/4).toFixed(2),565,yy+5,muted,12)
  }
  c.setLineDash([4,4]);c.strokeStyle='#406478';c.beginPath();c.moveTo(25,y(state.initialCapital));c.lineTo(557,y(state.initialCapital));c.stroke();c.setLineDash([])
  c.beginPath();values.forEach((v,i)=>i?c.lineTo(x(i),y(v)):c.moveTo(x(i),y(v)))
  c.strokeStyle=orange;c.lineWidth=2;c.stroke()
  text('OPEN',25,355,muted,12);text(`BAR ${state.bar+1} / ${state.bars}`,445,355,muted,12)
  panel(right,top,rightW,chartH,'ORDER / ACCOUNT MONITOR')
  const rows=[['DECISION',arm.side],['EXECUTION',arm.exec],['FILLS',String(arm.fills)],['VETOES',String(arm.vetoes)],['FEES PAID',Number(arm.fees).toFixed(4)],['START CAPITAL',state.initialCapital.toFixed(2)],['MARKET',state.product],['MODE','PAPER ONLY']]
  rows.forEach(([label,value],i)=>{const yy=150+i*27;if(i%2===0)rect(right+1,yy-17,rightW-2,25,'#0b1420');text(label,right+9,yy,muted,13);text(value,right+178,yy,i<2?(value==='BUY'||value==='FILLED'?green:value==='SELL'||value==='VETO'?red:yellow):cyan,14)})
  panel(12,376,618,133,'TIME & SALES / RECENT FILLS')
  text('SIDE     QUANTITY / PRICE',23,420,muted,13)
  if(!arm.trades.length) text('Awaiting first execution',23,448,muted,14)
  arm.trades.slice(0,4).forEach((trade,i)=>text(trade.label.slice(0,67),23,442+i*19,trade.side==='BUY'?green:trade.side==='SELL'?red:yellow,13))
  panel(640,376,rightW,133,arm.neural?'CONNECTOME / SIGNAL MONITOR':'STRATEGY / SIMULATED SIGNAL')
  text(arm.signalLine,650,425,cyan,18)
  text(arm.neural?(arm.learning?'MEMORY UPDATES: ENABLED':'MEMORY UPDATES: FROZEN'):'PROCEDURAL / NOT NEURAL',650,451,orange,13)
  text(arm.memoryLine.slice(0,39),650,478,yellow,13)
  rect(6,522,w-12,26,'#172635');text(`STATUS  ${arm.exec}  |  ${arm.reason.slice(0,102)}`,14,540,yellow,13)
  rect(6,555,w-12,26,'#080f16');text(`> ${state.product}   PORTFOLIO <GO>`,14,574,orange,16)
  if(Math.sin(time*3)>0)rect(410,560,9,16,orange)
  text('F1 HELP     F2 MARKET     F3 ORDERS     F4 BRAIN     F5 ACCOUNT',12,h-12,muted,12)
  // Keep the data readable: fills flash the border, never wash out the entire screen.
  if(flash>0){c.globalAlpha=flash;c.strokeStyle=arm.exec==='VETO'||arm.side==='SELL'?red:green;c.lineWidth=5;c.strokeRect(3,3,w-6,h-6);c.globalAlpha=1}
  terminal.texture.needsUpdate=true
}
