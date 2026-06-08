import { useSymbolsStore } from "../store/Symbols.js";
import { useSymbolsRender } from "../utils/useRender.js";
export var BracketsLeft = props => {
  var _useSymbolsStore = useSymbolsStore(),
    _useSymbolsStore$Brac = _useSymbolsStore.BracketsLeft,
    Comp = _useSymbolsStore$Brac === void 0 ? {} : _useSymbolsStore$Brac;
  useSymbolsRender(Comp, props, 'BracketsLeft');
  return null;
};
BracketsLeft.displayName = 'JVR.BracketsLeft';