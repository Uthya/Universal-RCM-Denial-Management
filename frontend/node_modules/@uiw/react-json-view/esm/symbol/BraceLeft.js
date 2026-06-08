import { useSymbolsStore } from "../store/Symbols.js";
import { useSymbolsRender } from "../utils/useRender.js";
export var BraceLeft = props => {
  var _useSymbolsStore = useSymbolsStore(),
    _useSymbolsStore$Brac = _useSymbolsStore.BraceLeft,
    Comp = _useSymbolsStore$Brac === void 0 ? {} : _useSymbolsStore$Brac;
  useSymbolsRender(Comp, props, 'BraceLeft');
  return null;
};
BraceLeft.displayName = 'JVR.BraceLeft';