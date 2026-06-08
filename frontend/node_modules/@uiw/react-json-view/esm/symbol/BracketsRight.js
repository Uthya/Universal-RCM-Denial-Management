import { useSymbolsStore } from "../store/Symbols.js";
import { useSymbolsRender } from "../utils/useRender.js";
export var BracketsRight = props => {
  var _useSymbolsStore = useSymbolsStore(),
    _useSymbolsStore$Brac = _useSymbolsStore.BracketsRight,
    Comp = _useSymbolsStore$Brac === void 0 ? {} : _useSymbolsStore$Brac;
  useSymbolsRender(Comp, props, 'BracketsRight');
  return null;
};
BracketsRight.displayName = 'JVR.BracketsRight';