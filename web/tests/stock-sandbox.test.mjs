import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import ts from 'typescript'

// Compile these pure modules in memory; no browser or emitted test files required.
const compile = path => ts.transpileModule(readFileSync(new URL(path,import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText
const moduleURL = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`
const liveURL=moduleURL(compile('../src/lib/live.ts'))
const {stockSandbox,STOCKS}=await import(moduleURL(compile('../src/lib/stockSandbox.ts').replace("'./live'",JSON.stringify(liveURL))))
const arms=[{id:'gordon',name:'Gordon'},{id:'warren',name:'Warren'}]

for(const symbol of Object.keys(STOCKS)) test(`${symbol}: every fill reconciles cash, shares, fees and equity`,()=> {
  const run=stockSandbox(symbol,arms)
  assert.equal(run.observations.length,180)
  assert.deepEqual(run,stockSandbox(symbol,arms))
  for(const arm of run.arms) {
    let cash=100, shares=0, fees=0, fills=0
    const sides=new Set()
    for(const observation of run.observations) {
      const entry=observation.arms[arm.id], fill=entry.execution.fill
      assert.equal(entry.signal.signal_source,'procedural')
      assert.equal(entry.signal.memory.enabled,false)
      if(fill) {
        const quantity=Number(fill.base),quote=Number(fill.quote),fee=Number(fill.fee)
        assert.ok(quote<=10+1e-8)
        assert.ok(Math.abs(fee-quote*.006)<1e-9)
        sides.add(entry.decision.side)
        cash+=entry.decision.side==='BUY'?-quote-fee:quote-fee
        shares+=entry.decision.side==='BUY'?quantity:-quantity
        fees+=fee;fills++
      }
      assert.ok(cash>=-1e-8&&shares>=-1e-8)
      assert.ok(Math.abs(cash-Number(entry.portfolio.cash))<1e-8)
      assert.ok(Math.abs(shares-Number(entry.portfolio.positions[run.product]))<1e-8)
      assert.ok(Math.abs(Number(entry.portfolio.equity)-(cash+shares*Number(observation.market.bid)))<1e-8)
      assert.equal(entry.portfolio.fills,fills)
    }
    assert.equal(run.summaries[arm.id].trades.length,fills)
    assert.ok(Math.abs(Number(run.summaries[arm.id].fees_paid)-fees)<1e-8)
    assert.deepEqual([...sides].sort(),['BUY','SELL'])
  }
})
