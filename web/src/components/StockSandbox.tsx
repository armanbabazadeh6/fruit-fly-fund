import { useEffect, useMemo, useState } from 'react'
import type { ArmMeta } from '../lib/types'
import { STOCKS, stockSandbox, type StockSymbol } from '../lib/stockSandbox'
import { TradingFloor } from './TradingFloor'
import { Transport } from './Transport'
import { PriceChart } from './PriceChart'
import { TradeHistory } from './TradeHistory'

export function StockSandbox({arms,onClose}:{arms:ArmMeta[];onClose:()=>void}) {
  const [symbol,setSymbol]=useState<StockSymbol>('NVDA')
  const [index,setIndex]=useState(0)
  const [playing,setPlaying]=useState(true)
  const [speed,setSpeed]=useState(1)
  const data=useMemo(()=>stockSandbox(symbol,arms),[symbol,arms])
  useEffect(()=> {
    if(!playing)return
    const timer=window.setInterval(()=>setIndex(i=>{if(i>=179){setPlaying(false);return i}return i+1}),1000/speed)
    return ()=>clearInterval(timer)
  },[playing,speed])
  return <div className="app"><header className="sandbox-header"><strong>fruit fly fund®</strong><button className="chip floor-mode" onClick={onClose}>← Neural recordings</button></header><main className="app-main">
    <section className="lab-intro"><div><p className="eyebrow">THE STOCK SANDBOX</p><h1>Time to make<br/><em>some buzz.</em></h1><p>Watch Gordon follow momentum and Warren trade against it. Synthetic stock prices, simulated strategies, paper dollars.</p></div><div className="lab-stamp"><strong>{symbol}</strong><span>{STOCKS[symbol].name}</span><small>FICTIONAL MARKET · NOT NEURAL</small></div></section>
    <div className="sandbox-controls"><div>{(Object.keys(STOCKS) as StockSymbol[]).map(ticker=><button className={`chip floor-mode ${ticker===symbol?'is-on':''}`} aria-pressed={ticker===symbol} key={ticker} onClick={()=>{setSymbol(ticker);setIndex(0);setPlaying(true)}}>{ticker} · {STOCKS[ticker].name}</button>)}</div><span className="chip chip-warn">Synthetic stock sandbox · no live quotes</span></div>
    <Transport index={index} bars={180} playing={playing} live={false} firstTimestamp={data.seasonBars[0].t} timestamp={data.seasonBars[index].t} onSeek={i=>{setIndex(i);setPlaying(false)}} onPlayToggle={()=>{if(index===179)setIndex(0);setPlaying(!playing)}} onStep={delta=>{setPlaying(false);setIndex(i=>Math.max(0,Math.min(179,i+delta)))}} speed={speed} onSpeed={setSpeed}/>
    <TradingFloor {...data} index={index} engine="procedural" live={false} initialCapital="100"/>
    <PriceChart times={data.seasonBars.map(b=>b.t)} mids={data.seasonBars.map(b=>b.mid)} selectedIndex={index} onSelect={i=>{setIndex(i);setPlaying(false)}} product={data.product} visibleBars={index+1}/>
    <TradeHistory arms={data.arms} summaries={data.summaries} selectedIndex={index} onSelectBar={i=>{setIndex(i);setPlaying(false)}} firstBarT={data.seasonBars[0].t}/>
    <p className="muted">This sandbox uses fictional price paths and two programmed strategies. It does not run a fruit-fly brain. Switch to neural recordings to inspect measured brain activity. Each account starts with $100 and pays 0.6% per fill.</p>
  </main></div>
}
