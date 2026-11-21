import {request,post}from'./client.js';
export const getBudget=(planId)=>request(planId?`/shares/budget?plan_id=${planId}`:'/shares/budget');
export const getShares=(planId)=>request(planId?`/shares?plan_id=${planId}`:'/shares');
export const createShare=data=>post('/shares',data);
export const retryShare=id=>post(`/shares/${id}/retry`,{});
export const previewDp=(reportId,epsilon)=>request(`/reports/${reportId}/dp-preview?epsilon=${epsilon}`);
export const shareDownloadUrl=id=>`/api/shares/${id}/download`;
export const publicDownloadUrl=token=>`/api/public/shares/${token}`;
