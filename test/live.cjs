const {getStreams}=require('../providers/abalatam.js');
(async()=>{for(const [id,t,s,e] of [[272,'movie'],[155,'movie'],[49444,'movie'],[65334,'tv',6,2],[65334,'tv',1,3],[999999999,'movie']]){
const r=await getStreams(id,t,s,e);console.log(id,t,s,e,'=>',r.length,r[0]&&r[0].title);
if(r[0]){const x=await fetch(r[0].url,{headers:r[0].headers});console.log(' m3u8',x.status,(await x.text()).slice(0,20).replace(/\n/g,' '))}}})()
