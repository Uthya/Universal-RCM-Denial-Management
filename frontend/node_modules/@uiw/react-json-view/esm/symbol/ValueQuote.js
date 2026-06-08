import { useSymbolsStore } from "../store/Symbols.js";
import { useSymbolsRender } from "../utils/useRender.js";
export var ValueQuote = props => {
  var _useSymbolsStore = useSymbolsStore(),
    _useSymbolsStore$Valu = _useSymbolsStore.ValueQuote,
    Comp = _useSymbolsStore$Valu === void 0 ? {} : _useSymbolsStore$Valu;
  useSymbolsRender(Comp, props, 'ValueQuote');
  return null;
};
ValueQuote.displayName = 'JVR.ValueQuote';