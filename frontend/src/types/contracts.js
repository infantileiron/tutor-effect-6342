/** @typedef {{id:number,code:string,name:string,campus:string,group:'control'|'intervention',score:number|null,absent:boolean}} Student */
/** @typedef {{id:number,title:string,campus:string,exclude_absent:boolean,description:string}} Plan */
/** @typedef {{id:number,plan_id:number,status:string,result:object|null,conclusion:string,published:boolean}} Report */
/** @typedef {'pending'|'processing'|'ready'|'failed'} ShareStatus */
/** @typedef {{group:string,n:number,private_mean:number,error_budget:{sensitivity_replace_one:number,laplace_scale_b:number,expected_abs_error_mae:number,rmse:number,prob_95pct_abs_error_within:number,prob_99pct_abs_error_within:number,clipped_to_domain:boolean}}} PublicGroup */
/** @typedef {{id:number,report_id:number,plan_id:number,idempotency_key:string,epsilon:number,status:ShareStatus,download_token:string|null,public_payload:{epsilon:number,groups:PublicGroup[]}|null,fail_injected:boolean,attempts:number,error:string,created_at:string}} ShareRequest */
/** @typedef {{plan_id:number,cap_epsilon:number,spent_epsilon:number,remaining_epsilon:number}} PrivacyBudget */
export const contractVersion=2;
