import * as THREE from 'three'

/** Schematic regions. Positions and paths are illustrative, never anatomical cell coordinates. */
export function buildBrain() {
  const root = new THREE.Group()
  const locations = [[-.34,.06,0],[.34,.06,0],[-.19,.27,-.08],[.19,.27,-.08],[0,-.13,.1],[0,.1,.17]]
  const colors = [0x64dfff,0x64dfff,0xc7e8ad,0xffb454,0xcc9eff,0xff7793]
  const nodes: THREE.Mesh[][] = []
  const shell = new THREE.Mesh(new THREE.SphereGeometry(.57,32,24),new THREE.MeshBasicMaterial({color:0x66d9e8,wireframe:true,transparent:true,opacity:.055,depthWrite:false}))
  shell.scale.set(1.15,.85,.8)
  root.add(shell)
  const lines = new THREE.Group()
  root.add(lines)
  locations.forEach((location,group) => {
    const cluster: THREE.Mesh[] = []
    for(let i=0;i<22;i++) {
      const angle=i*2.39996
      const radius=.13*Math.sqrt((i+1)/22)
      const point=new THREE.Vector3(location[0]+Math.cos(angle)*radius,location[1]+Math.sin(angle)*radius,location[2]+Math.sin(i*4.1)*.085)
      const dot=new THREE.Mesh(new THREE.SphereGeometry(i%5===0?.022:.012,6,5),new THREE.MeshBasicMaterial({color:colors[group],transparent:true,opacity:.5,depthWrite:false}))
      dot.position.copy(point)
      root.add(dot); cluster.push(dot)
      if(i>0) {
        const geometry=new THREE.BufferGeometry().setFromPoints([cluster[i-1].position,point])
        lines.add(new THREE.Line(geometry,new THREE.LineBasicMaterial({color:colors[group],transparent:true,opacity:.18,depthWrite:false})))
      }
    }
    nodes.push(cluster)
  })
  for(let i=0;i<locations.length;i++) {
    const curve=new THREE.QuadraticBezierCurve3(new THREE.Vector3(...locations[i]),new THREE.Vector3(0,.35,.22),new THREE.Vector3(...locations[(i+1)%locations.length]))
    lines.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(curve.getPoints(24)),new THREE.LineBasicMaterial({color:0x84bac5,transparent:true,opacity:.22})))
  }
  root.visible=false
  return {root,update(values:number[],time:number) {
    nodes.forEach((cluster,g)=> {
      const activity=Math.min(1,Math.log1p(Math.max(0,values[g]??0))/Math.log(101))
      cluster.forEach((dot,i)=> {
        const pulse=.65+.35*Math.sin(time*3+i*.7)
        ;(dot.material as THREE.MeshBasicMaterial).opacity=.12+activity*pulse*.88
        dot.scale.setScalar(.7+activity*pulse*.8)
      })
    })
  }}
}
