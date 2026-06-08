import { useSymbolsStore } from "../store/Symbols.js";
import { useSymbolsRender } from "../utils/useRender.js";
export var BraceRight = props => {
  var _useSymbolsStore = useSymbolsStore(),
    _useSymbolsStore$Brac = _useSymbolsStore.BraceRight,
    Comp = _useSymbolsStore$Brac === void 0 ? {} : _useSymbolsStore$Brac;
  useSymbolsRender(Comp, props, 'BraceRight');
  return null;
};
BraceRight.displayName = 'JVR.BraceRight';