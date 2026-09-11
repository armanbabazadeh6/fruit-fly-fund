import type { ArmMeta, Observation, ProceduralSignal, Trade } from './types'
import { summarise } from './live'

export const STOCKS = { AAPL: { name:'Apple', start:200 }, NVDA: { name:'NVIDIA', start:120 }, TSLA: { name:'Tesla', start:250 } }
export type StockSymbol = keyof typeof STOCKS

/** Reproducible fictional prices and paper accounting. Never a market feed or neural run. */
export function stockSandbox(symbol: StockSymbol, originalArms: ArmMeta[]) {
  const bars=180, capital=100, feeRate=.006
  const product=`${symbol}-USD`
  const arms=originalArms.map((arm,i)=>({...arm,learning:false,role_label:i===0?'Momentum strategy':'Mean reversion strategy',starting_capital:String(capital),backend:{signal_source:'procedural' as const,label:'Stock sandbox — not neural',simulated:true},detail:'Deterministic stock sandbox. No connectome is running.',tagline:'Synthetic prices. Simulated decisions.'}))
  const seasonBars=Array.from({length:bars},(_,i)=> {
    const mid=Number((STOCKS[symbol].start*(1+.018*Math.sin(i*.17)+.008*Math.sin(i*.61)+i*.0001)).toFixed(2))
    return {t:1767277800+i*60,mid,bid:(mid*.99975).toFixed(4),ask:(mid*1.00025).toFixed(4)}
  })
  const accounts=arms.map(()=>({cash:capital,shares:0,fees:0,fills:0,trades:[] as Trade[]}))
  const observations:Observation[]=seasonBars.map((bar,i)=> {
    const entries:Observation['arms']={}
    arms.forEach((arm,a)=> {
      const account=accounts[a]
      const previous=seasonBars[Math.max(0,i-4)].mid
      const momentum=(bar.mid/previous-1)*100
      const score=a===0?momentum:-momentum
      const side=i%3===0&&Math.abs(score)>.15?(score>0?'BUY':'SELL'):'HOLD'
      const price=Number(side==='BUY'?bar.ask:bar.bid)
      const quantity=side==='BUY'?Math.floor(Math.min(10,account.cash/(1+feeRate))/price*1e8)/1e8:side==='SELL'?Math.floor(Math.min(account.shares,10/price)*1e8)/1e8:0
      const filled=quantity>0.00000001
      const quote=quantity*price, fee=filled?quote*feeRate:0
      if(filled) {
        account.cash+=side==='BUY'?-quote-fee:quote-fee
        account.shares+=side==='BUY'?quantity:-quantity
        account.fees+=fee;account.fills++
      }
      const equity=account.cash+account.shares*Number(bar.bid)
      const reason=`Synthetic ${symbol}: ${a===0?'follow':'fade'} four-bar momentum (${momentum.toFixed(3)}%). Paper sandbox only.`
      const signal:ProceduralSignal={signal_source:'procedural',simulated:true,label:'Stock sandbox — not neural',side,score,threshold:.15,momentum,volatility:0,lookback:4,simulated_memory_delta:null,base_score:score,compute_seconds:0,stimulus:'none',memory:{enabled:false,simulated:true,changed_edges:null}}
      entries[arm.id]={signal,decision:{side,explanation:{kind:'procedural',rule:'stock-sandbox',measured:{score},steps:[reason],result:side}},execution:{status:filled?'FILLED':'HOLD',reason,fill:filled?{mode:'paper',status:'FILLED',base:String(quantity),quote:String(quote),fee:String(fee),price:String(price)}:null},portfolio:{cash:String(account.cash),positions:{[product]:String(account.shares)},equity:String(equity),return_pct:(equity/capital-1)*100,fees_paid:String(account.fees),fills:account.fills,vetoes:0,halted:null},stimulus:{kind:'none',delta_usdc:'0'},compute_seconds:0}
      if(filled) account.trades.push({i,t:bar.t,side:side as 'BUY'|'SELL',product,base_size:String(quantity),price:String(price),quote_size:String(quote),fee:String(fee),reason,equity_after:String(equity),memory_changed_edges:null})
    })
    return {i,t:bar.t,product,market:{bid:bar.bid,ask:bar.ask,mid:bar.mid},arms:entries,same_frame_both_arms:true,same_neural_input_both_arms:false}
  })
  const summaries=summarise(arms,observations)
  arms.forEach((arm,i)=>{
    const summary=summaries[arm.id]
    summary.trades=accounts[i].trades
    summary.deployment={...summary.deployment,bars_holding:summary.exposure_bars,holding_fraction:summary.exposure_bars/bars,daily_order_limit:60,cooldown_seconds:180}
  })
  return {arms,observations,seasonBars,summaries,product}
}
