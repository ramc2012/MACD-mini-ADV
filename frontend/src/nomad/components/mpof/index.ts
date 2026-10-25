/**
 * MP+OF visualization primitives — shared by the Institutional Convergence,
 * Auction Intelligence, Fractal and Commodity MP+OF lanes.
 *
 * Design rule for the module: every order-flow block must carry its source
 * honesty (TICK/BOOK QUOTES vs BAR PROXY — sides ALWAYS inferred, there is no
 * aggressor trade tape) and its data timestamp. Components here bake both in
 * so desks can't accidentally render inferred sides as measured ones.
 */
export { ProfileLadder, type ProfileLadderProps } from "./ProfileLadder";
export { CvdPanel, type CvdPoint, type CvdDivergence } from "./CvdPanel";
export { FootprintGrid, type FootprintBar, type FootprintLevel } from "./FootprintGrid";
export { OrderFlowPulse, type FlowTrade } from "./OrderFlowPulse";
export { LiveOrderFlowTape } from "./LiveOrderFlowTape";
export { GateChips } from "./GateChips";
export { OfSourceBadge, classifyOfSource, type OfSourceKind, type OfSourceClass } from "./OfSourceBadge";
