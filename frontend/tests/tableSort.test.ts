import {test} from 'node:test';
import assert from 'node:assert/strict';
import {sortKey,compareKeys} from '../src/tableSort.ts';
const order=(cells:string[],dir:1|-1=1)=>[...cells].sort((a,b)=>compareKeys(sortKey(a),sortKey(b),dir));
test('rupees, signs, percents and ranks sort as numbers',()=>{
 assert.deepEqual(order(['₹2,28,250','₹51,000','-₹1,200','+₹9,99,999']),['-₹1,200','₹51,000','₹2,28,250','+₹9,99,999']);
 assert.deepEqual(order(['#10','#2','#1']),['#1','#2','#10']);
 assert.deepEqual(order(['-3.5%','12%','0.4%'],-1),['12%','0.4%','-3.5%']);
 assert.deepEqual(order(['−2','1']),['−2','1']);
});
test('IST timestamps sort chronologically and blanks stay last both ways',()=>{
 const t=['2026-10-28 09:20:00 IST','—','2026-10-27 14:47:00 IST'];
 assert.deepEqual(order(t),['2026-10-27 14:47:00 IST','2026-10-28 09:20:00 IST','—']);
 assert.deepEqual(order(t,-1),['2026-10-28 09:20:00 IST','2026-10-27 14:47:00 IST','—']);
});
test('text sorts naturally, case-insensitively',()=>{
 assert.deepEqual(order(['sbin · PE','RELIANCE · CE','KEI 10 CE','KEI 9 CE']),['KEI 9 CE','KEI 10 CE','RELIANCE · CE','sbin · PE']);
});
test('a rupee change with its percentage sorts by the percentage',()=>{
 assert.deepEqual(order(['-765.00 (-2.21%)','-210.40 (-6.28%)','+25.40 (+1.94%)','−440.00 (−4.04%)']),
  ['-210.40 (-6.28%)','−440.00 (−4.04%)','-765.00 (-2.21%)','+25.40 (+1.94%)']);
});
