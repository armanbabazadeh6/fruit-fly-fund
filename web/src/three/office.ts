import * as THREE from 'three'

/** A fictional downtown skyline, modeled in 3D beyond a three-sided curtain wall. */
export function buildOffice() {
  const office = new THREE.Group()
  office.name = 'glass-office'
  const metal = new THREE.MeshStandardMaterial({ color: 0x182c35, metalness: .75, roughness: .28 })
  const glass = new THREE.MeshPhysicalMaterial({ color: 0xa0d5eb, transparent: true, opacity: .055, roughness: .08, metalness: .25, side: THREE.DoubleSide, depthWrite: false })
  const box = (w:number,h:number,d:number,x:number,y:number,z:number,material:THREE.Material) => {
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(w,h,d), material)
    mesh.position.set(x,y,z)
    office.add(mesh)
    return mesh
  }
  const skyCanvas=document.createElement('canvas')
  skyCanvas.width=16; skyCanvas.height=512
  const ctx=skyCanvas.getContext('2d')!
  const sky=ctx.createLinearGradient(0,0,0,512)
  sky.addColorStop(0,'#112840');sky.addColorStop(.38,'#497894');sky.addColorStop(.7,'#e1ad85');sky.addColorStop(1,'#657e91')
  ctx.fillStyle=sky;ctx.fillRect(0,0,16,512)
  const skyTexture=new THREE.CanvasTexture(skyCanvas)
  skyTexture.colorSpace=THREE.SRGBColorSpace
  const skyMesh=new THREE.Mesh(new THREE.SphereGeometry(85,32,16),new THREE.MeshBasicMaterial({map:skyTexture,side:THREE.BackSide,depthWrite:false,fog:false}))
  office.add(skyMesh)

  // Building silhouettes and lit office windows are instanced to keep the scene light.
  const buildingMaterial=new THREE.MeshStandardMaterial({color:0x2b475c,roughness:.5,metalness:.45})
  const towerGeometry=new THREE.BoxGeometry(1,1,1)
  const towers=new THREE.InstancedMesh(towerGeometry,buildingMaterial,48)
  const windows=new THREE.InstancedMesh(new THREE.PlaneGeometry(.12,.18),new THREE.MeshBasicMaterial({color:0xffdfab,toneMapped:false}),5000)
  const transform=new THREE.Object3D()
  let windowIndex=0
  const random=(i:number)=>{const v=Math.sin(i*127.1+311.7)*43758.5453;return v-Math.floor(v)}
  for(let i=0;i<48;i++) {
    const row=Math.floor(i/16), column=i%16
    const x=(column-7.5)*2.3+row*.6
    const height=3+random(i+4)*9
    const width=1.1+random(i+65)*.7
    const z=-11-row*8
    const bottom=-7-row*.8
    transform.position.set(x,bottom+height/2,z);transform.scale.set(width,height,1.5);transform.rotation.set(0,0,0);transform.updateMatrix()
    towers.setMatrixAt(i,transform.matrix)
    towers.setColorAt(i,new THREE.Color().setHSL(.58,.22,.17+row*.055+random(i)*.09))
    for(let y=bottom+.3;y<bottom+height-.2;y+=.39) for(let col=0;col<4;col++) {
      if(random(i*321+col*43+Math.round(y*10))<.42) continue
      transform.position.set(x+(col-1.5)*width/5,y,z+.76);transform.scale.set(1,1,1);transform.updateMatrix()
      windows.setMatrixAt(windowIndex,transform.matrix)
      windows.setColorAt(windowIndex,new THREE.Color(random(windowIndex)>.45?0xffd39b:0x91b9d0))
      windowIndex++
    }
    // Rooftop crowns and antennas make the silhouette read as downtown towers.
    if(i%4===0) {
      box(width*.6,.5,1,x,bottom+height+.25,z,buildingMaterial)
      box(.035,1.3,.035,x,bottom+height+.95,z,metal)
    }
  }
  windows.count=windowIndex
  office.add(towers,windows)

  // Floor-to-ceiling glass wraps behind both desks and down both sides.
  for(let i=-4;i<=4;i++) box(.075,7,.13,i*2,-1.55+3.5,-5.8,metal)
  for(const y of [-1.5,1.3,5.45]) box(16,.085,.16,0,y,-5.8,metal)
  for(let i=-4;i<4;i++) box(1.92,6.9,.025,i*2+1,1.95,-5.8,glass)
  for(const side of [-1,1]) {
    for(let z=-5;z<=5;z+=2) {
      box(.13,7,.075,side*8,1.95,z,metal)
      box(.025,6.9,1.92,side*8,1.95,z+1,glass)
    }
    for(const y of [-1.5,1.3,5.45]) box(.16,.085,12,side*8,y,0,metal)
    // Interior uplighting, window sills, and overhead light channels.
    box(.28,.12,12,side*7.9,-1.35,0,metal)
    box(.045,.035,11,side*6.8,5.1,0,new THREE.MeshBasicMaterial({color:0xffe1ad}))
  }
  box(16,.16,12,0,5.6,0,new THREE.MeshStandardMaterial({color:0x26323a,roughness:.8}))
  box(16,.12,.32,0,-1.35,-5.7,metal)
  // Subtle reflected light streaks on the interior glass.
  const reflection=new THREE.MeshBasicMaterial({color:0xb8e2ff,transparent:true,opacity:.055,depthWrite:false})
  for(const x of [-6.5,-2.5,2.5,6.5]) {
    const streak=box(.1,5,.01,x,2,-5.71,reflection)
    streak.rotation.z=-.27
  }
  const daylight=new THREE.DirectionalLight(0xa8d9ff,1.5)
  daylight.position.set(-5,5,-8)
  office.add(daylight)
  return office
}
