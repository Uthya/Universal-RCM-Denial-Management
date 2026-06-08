import { useSectionStore } from "../store/Section.js";
import { useSectionRender } from "../utils/useRender.js";
export var Copied = props => {
  var _useSectionStore = useSectionStore(),
    _useSectionStore$Copi = _useSectionStore.Copied,
    Comp = _useSectionStore$Copi === void 0 ? {} : _useSectionStore$Copi;
  useSectionRender(Comp, props, 'Copied');
  return null;
};
Copied.displayName = 'JVR.Copied';