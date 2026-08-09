/**
 * Ambient declarations for non-code imports.
 *
 * Next generates `next-env.d.ts` during a build, but that file is generated
 * (and git-ignored), so a clean checkout running `tsc --noEmit` before a build
 * would fail on the stylesheet import in the root layout. Declaring these here
 * makes type-checking independent of build order.
 */

declare module "*.css";
declare module "*.svg" {
  const content: string;
  export default content;
}
