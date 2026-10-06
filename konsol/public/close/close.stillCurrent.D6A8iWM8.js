async function s(t,r){const e=t();let a;try{a=await r()}catch(l){if(t()!==e)return{stale:!0};throw l}return t()===e?{stale:!1,value:a}:{stale:!0}}export{s as w};
